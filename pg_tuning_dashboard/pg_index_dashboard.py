import json
import os
import re
import tkinter as tk
from tkinter import messagebox, ttk

import customtkinter as ctk
import psycopg2

# 테마 및 모드 설정
ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

CONFIG_FILE = "db_config.json"


class PGPerformanceDashboard(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("PostgreSQL 성능 모니터링 & 인덱스 정밀 조절 브라우저")
        self.geometry("1280x980")

        self.conn = None
        self.sort_reverse = {}
        self.current_menu = None

        # 가이드 도움말 딕셔너리
        self.column_guides = {
            "disk": (
                "💡 [테이블별 디스크 사용량 분석 가이드]\n"
                "• total_size가 수 GB 이상으로 비대하다면 파티셔닝이나 데이터 아카이빙(이관)을 검토하세요.\n"
                "• index_size가 data_size보다 과도하게 크다면 불필요하거나 중복된 인덱스가 많다는 방증입니다."
            ),
            "slow": (
                "💡 [가장 느린 쿼리 분석 가이드]\n"
                "• total_time_ms: 시스템 전체에서 이 쿼리가 누적으로 소모한 총 시간입니다. (가장 중요)\n"
                "• avg_time_ms: 건당 평균 수행 시간입니다. 이 값이 크다면 인덱스 부재 확률이 높습니다."
            ),
            "seq": (
                "💡 [Full Scan 빈번한 테이블 분석 가이드]\n"
                "• seq_scan / seq_tup_read가 모두 높다면 WHERE 조건절에 인덱스 추가가 시급합니다."
            ),
            "index": (
                "💡 [인덱스 미사용 테이블 분석 가이드]\n"
                "• idx_scan = 0인 인덱스는 CUD 성능만 갉아먹으므로 안전하게 삭제를 검토하세요."
            ),
            "dead": (
                "💡 [Dead Tuples / 진공 대상 분석 가이드]\n"
                "• dead_tuple_ratio_pct가 15%가 넘어가면 쿼리 속도가 느려지므로 수동 VACUUM을 검토하세요."
            ),
            "idx_cache": (
                "💡 [인덱스 캐시 히트율 가이드]\n"
                "• cache_hit_pct가 99% 이상인 것이 이상적입니다.\n"
                "• 히트율이 지속적으로 낮다면 shared_buffers 설정 증설이나 디스크 I/O 병목을 점검하세요."
            ),
            "idx_bloat": (
                "💡 [인덱스 Bloat(단편화) 진단 가이드]\n"
                "• index_ratio_pct가 100% 이상(인덱스가 테이블보다 큼)이거나 수치가 과도하게 크다면 인덱스 단편화를 의심해볼 수 있습니다.\n"
                "• 주기적인 REINDEX CONCURRENTLY 실행을 권장합니다."
            ),
            "idx_progress": (
                "💡 [진행 중인 인덱스 작업 모니터링 가이드]\n"
                "• CREATE INDEX CONCURRENTLY 또는 REINDEX의 실시간 진행 단계(phase) 및 블록/튜플 처리율을 확인합니다."
            ),
        }

        # UI 셋업
        self.setup_ui()
        self.load_saved_config()

    def setup_ui(self):
        # 1. 상단 프레임
        self.top_frame = ctk.CTkFrame(self, height=60, corner_radius=0)
        self.top_frame.pack(side="top", fill="x", padx=10, pady=5)

        self.title_label = ctk.CTkLabel(
            self.top_frame,
            text="PG Performance & Index Control Monitor",
            font=ctk.CTkFont(size=20, weight="bold"),
        )
        self.title_label.pack(side="left", padx=20, pady=10)

        self.status_label = ctk.CTkLabel(
            self.top_frame, text="상태: 연결 대기 중", font=ctk.CTkFont(size=12)
        )
        self.status_label.pack(side="right", padx=20, pady=10)

        # 2. 좌측 메뉴 프레임
        self.menu_frame = ctk.CTkFrame(self, width=280, corner_radius=10)
        self.menu_frame.pack(side="left", fill="y", padx=10, pady=10)

        self.conn_title = ctk.CTkLabel(
            self.menu_frame, text="🔌 DB 접속 설정", font=ctk.CTkFont(size=13, weight="bold")
        )
        self.conn_title.pack(padx=10, pady=(10, 5), anchor="w")

        # DB 접속 입력 필드
        row1_frame = ctk.CTkFrame(self.menu_frame, fg_color="transparent")
        row1_frame.pack(fill="x", padx=5, pady=2)

        self.ent_host = ctk.CTkEntry(
            row1_frame, placeholder_text="Host", height=24, font=ctk.CTkFont(size=11)
        )
        self.ent_host.pack(side="left", expand=True, fill="x", padx=2)
        self.ent_port = ctk.CTkEntry(
            row1_frame, placeholder_text="Port", width=65, height=24, font=ctk.CTkFont(size=11)
        )
        self.ent_port.pack(side="right", padx=2)

        row2_frame = ctk.CTkFrame(self.menu_frame, fg_color="transparent")
        row2_frame.pack(fill="x", padx=5, pady=2)

        self.ent_dbname = ctk.CTkEntry(
            row2_frame, placeholder_text="DB Name", height=24, font=ctk.CTkFont(size=11)
        )
        self.ent_dbname.pack(side="left", expand=True, fill="x", padx=2)
        self.ent_user = ctk.CTkEntry(
            row2_frame, placeholder_text="User", height=24, font=ctk.CTkFont(size=11)
        )
        self.ent_user.pack(side="right", expand=True, fill="x", padx=2)

        self.ent_password = ctk.CTkEntry(
            self.menu_frame,
            placeholder_text="Password",
            height=24,
            font=ctk.CTkFont(size=11),
            show="*",
        )
        self.ent_password.pack(fill="x", padx=7, pady=2)

        btn_connect = ctk.CTkButton(
            self.menu_frame,
            text="연결 및 저장",
            height=26,
            fg_color="#2b712b",
            hover_color="#1e4e1e",
            font=ctk.CTkFont(size=11, weight="bold"),
            command=self.save_and_connect_db,
        )
        btn_connect.pack(fill="x", padx=7, pady=(4, 8))

        lbl_sep1 = ctk.CTkLabel(
            self.menu_frame,
            text="────────────────────────",
            text_color="gray",
            font=ctk.CTkFont(size=10),
        )
        lbl_sep1.pack(padx=10, pady=1)

        # 시스템 분석 메뉴
        self.menu_title = ctk.CTkLabel(
            self.menu_frame, text="📊 일반 통계 분석", font=ctk.CTkFont(size=12, weight="bold")
        )
        self.menu_title.pack(padx=10, pady=2, anchor="w")

        btn_disk_usage = ctk.CTkButton(
            self.menu_frame, text="테이블별 디스크 사용량", height=24, command=self.click_disk_usage
        )
        btn_disk_usage.pack(fill="x", padx=10, pady=2)

        btn_slow_query = ctk.CTkButton(
            self.menu_frame,
            text="가장 느린 쿼리 (Top 10)",
            height=24,
            command=self.click_slow_queries,
        )
        btn_slow_query.pack(fill="x", padx=10, pady=2)

        btn_seq_scan = ctk.CTkButton(
            self.menu_frame,
            text="Full Scan 빈번한 테이블",
            height=24,
            command=self.click_sequential_scans,
        )
        btn_seq_scan.pack(fill="x", padx=10, pady=2)

        btn_dead_tuples = ctk.CTkButton(
            self.menu_frame,
            text="Dead Tuples (진공 대상)",
            height=24,
            command=self.click_dead_tuples,
        )
        btn_dead_tuples.pack(fill="x", padx=10, pady=2)

        lbl_sep2 = ctk.CTkLabel(
            self.menu_frame,
            text="────────────────────────",
            text_color="gray",
            font=ctk.CTkFont(size=10),
        )
        lbl_sep2.pack(padx=10, pady=1)

        # 인덱스 전문 모니터링 및 제어 메뉴
        self.idx_menu_title = ctk.CTkLabel(
            self.menu_frame,
            text="⚡ 인덱스 정밀 모니터링 & 제어",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#3B82F6",
        )
        self.idx_menu_title.pack(padx=10, pady=2, anchor="w")

        btn_index_usage = ctk.CTkButton(
            self.menu_frame,
            text="미사용 인덱스 진단",
            height=24,
            fg_color="#1f6aa5",
            command=self.click_unused_indexes,
        )
        btn_index_usage.pack(fill="x", padx=10, pady=2)

        btn_index_cache = ctk.CTkButton(
            self.menu_frame,
            text="인덱스 캐시 히트율",
            height=24,
            fg_color="#1f6aa5",
            command=self.click_index_cache,
        )
        btn_index_cache.pack(fill="x", padx=10, pady=2)

        btn_index_bloat = ctk.CTkButton(
            self.menu_frame,
            text="인덱스 Bloat(단편화) 추정",
            height=24,
            fg_color="#1f6aa5",
            command=self.click_index_bloat,
        )
        btn_index_bloat.pack(fill="x", padx=10, pady=2)

        btn_index_progress = ctk.CTkButton(
            self.menu_frame,
            text="진행 중인 인덱스 작업",
            height=24,
            fg_color="#1f6aa5",
            command=self.click_index_progress,
        )
        btn_index_progress.pack(fill="x", padx=10, pady=2)

        btn_manage_index = ctk.CTkButton(
            self.menu_frame,
            text="🛠️ 인덱스 생성 / 삭제 제어",
            height=28,
            fg_color="#D97706",
            hover_color="#B45309",
            font=ctk.CTkFont(size=11, weight="bold"),
            command=self.open_index_manager_dialog,
        )
        btn_manage_index.pack(fill="x", padx=10, pady=(6, 10))

        # 3. 우측 메인 프레임
        self.main_frame = ctk.CTkFrame(self, corner_radius=10)
        self.main_frame.pack(side="right", expand=True, fill="both", padx=10, pady=10)

        self.summary_frame = ctk.CTkFrame(
            self.main_frame, fg_color="#1c2b36", border_width=1, border_color="#2b3e4a", height=65
        )
        self.summary_frame.pack(fill="x", padx=20, pady=(15, 5))
        self.summary_frame.pack_propagate(False)

        self.lbl_curr_title = ctk.CTkLabel(
            self.summary_frame,
            text="🔌 현재 커넥션",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#9eb1bd",
        )
        self.lbl_curr_title.place(x=30, y=10)
        self.lbl_curr_val = ctk.CTkLabel(
            self.summary_frame,
            text="- 개",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color="white",
        )
        self.lbl_curr_val.place(x=30, y=30)

        self.lbl_max_title = ctk.CTkLabel(
            self.summary_frame,
            text="🚧 최종 한도 (Max)",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#9eb1bd",
        )
        self.lbl_max_title.place(x=250, y=10)
        self.lbl_max_val = ctk.CTkLabel(
            self.summary_frame,
            text="- 개",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color="white",
        )
        self.lbl_max_val.place(x=250, y=30)

        self.lbl_ratio_title = ctk.CTkLabel(
            self.summary_frame,
            text="📊 한도 점유율",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#9eb1bd",
        )
        self.lbl_ratio_title.place(x=470, y=10)
        self.lbl_ratio_val = ctk.CTkLabel(
            self.summary_frame,
            text="- %",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color="green",
        )
        self.lbl_ratio_val.place(x=470, y=28)

        # 컨트롤 바
        self.control_bar = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        self.control_bar.pack(fill="x", padx=20, pady=(10, 2))

        self.view_title = ctk.CTkLabel(
            self.control_bar,
            text="원하는 분석 메뉴를 선택하세요.",
            font=ctk.CTkFont(size=14, weight="bold"),
        )
        self.view_title.pack(side="left")

        self.btn_refresh_summary = ctk.CTkButton(
            self.control_bar,
            text="🔄 즉시 갱신",
            width=100,
            height=28,
            fg_color="#1f6aa5",
            hover_color="#144d78",
            font=ctk.CTkFont(size=11, weight="bold"),
            command=self.refresh_connection_summary,
        )
        self.btn_refresh_summary.pack(side="right")

        # 그리드 스타일
        style = ttk.Style()
        style.theme_use("default")
        style.configure(
            "Treeview",
            background="#2a2d2e",
            foreground="white",
            rowheight=28,
            fieldbackground="#2a2d2e",
            borderwidth=0,
        )
        style.map("Treeview", background=[("selected", "#1f538d")])
        style.configure("Treeview.Heading", background="#1f6aa5", foreground="white", relief="flat")
        style.map(
            "Treeview.Heading", background=[("active", "#254b73")], foreground=[("active", "white")]
        )

        # 그리드 뷰
        self.grid_frame = ctk.CTkFrame(self.main_frame)
        self.grid_frame.pack(expand=True, fill="both", padx=20, pady=5)

        self.tree_scroll = ttk.Scrollbar(self.grid_frame)
        self.tree_scroll.pack(side="right", fill="y")

        self.tree = ttk.Treeview(
            self.grid_frame, yscrollcommand=self.tree_scroll.set, selectmode="browse"
        )
        self.tree.pack(expand=True, fill="both")
        self.tree_scroll.config(command=self.tree.yview)

        # 가이드 박스
        self.guide_frame = ctk.CTkFrame(
            self.main_frame,
            fg_color="#1e2021",
            border_width=1,
            border_color="#3e4244",
            corner_radius=6,
        )
        self.guide_frame.pack(fill="x", padx=20, pady=(5, 2))

        self.guide_text_label = ctk.CTkLabel(
            self.guide_frame,
            text="분석 메뉴를 선택하시면 진단 길라잡이 가이드가 출력됩니다.",
            justify="left",
            anchor="w",
            font=ctk.CTkFont(size=12),
            text_color="#a8b2b8",
        )
        self.guide_text_label.pack(fill="x", padx=15, pady=8)

        # 디테일 하단 정보바
        self.detail_header_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        self.detail_header_frame.pack(fill="x", padx=20, pady=(5, 2))

        self.detail_label = ctk.CTkLabel(
            self.detail_header_frame,
            text="선택한 항목 상세 정보:",
            font=ctk.CTkFont(size=12, weight="bold"),
        )
        self.detail_label.pack(side="left")

        self.detail_text = ctk.CTkTextbox(self.main_frame, height=100, activate_scrollbars=True)
        self.detail_text.pack(fill="x", padx=20, pady=(0, 15))

        self.tree.bind("<<TreeviewSelect>>", self.on_grid_select)

    def load_saved_config(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    config = json.load(f)
                    self.ent_host.insert(0, config.get("host", "localhost"))
                    self.ent_port.insert(0, config.get("port", "5432"))
                    self.ent_dbname.insert(0, config.get("dbname", "postgres"))
                    self.ent_user.insert(0, config.get("user", "postgres"))
                    self.ent_password.insert(0, config.get("password", ""))
                    self.status_label.configure(
                        text="상태: 저장된 접속 정보 로드 완료", text_color="yellow"
                    )
            except Exception:
                pass

    def save_and_connect_db(self):
        config = {
            "host": self.ent_host.get(),
            "port": self.ent_port.get(),
            "dbname": self.ent_dbname.get(),
            "user": self.ent_user.get(),
            "password": self.ent_password.get(),
        }
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False, indent=4)
        except Exception as e:
            print(f"설정 저장 실패: {e}")

        try:
            db_uri = f"postgresql://{config['user']}:{config['password']}@{config['host']}:{config['port']}/{config['dbname']}?application_name=PG_Dashboard_Admin"
            if self.conn and not self.conn.closed:
                self.conn.close()

            self.conn = psycopg2.connect(db_uri)
            self.conn.autocommit = True  # CONCURRENTLY 옵션 사용을 위해 autocommit 설정
            self.status_label.configure(
                text="상태: DB 연결 성공 (autocommit 활성)", text_color="green"
            )
            self.refresh_connection_summary()
        except Exception as e:
            self.status_label.configure(text=f"상태: 연결 실패 ({e!s})", text_color="red")

    def execute_query(self, query, params=None):
        if not self.conn or self.conn.closed:
            self.status_label.configure(text="상태: DB 연결이 필요합니다.", text_color="red")
            return [], []
        try:
            with self.conn.cursor() as cursor:
                cursor.execute(query, params)
                if cursor.description:
                    columns = [desc[0] for desc in cursor.description]
                    rows = cursor.fetchall()
                    return columns, rows
                else:
                    return [], []
        except Exception as e:
            self.status_label.configure(text=f"쿼리 오류: {e!s}", text_color="red")
            return [], []

    def refresh_connection_summary(self):
        if not self.conn or self.conn.closed:
            return

        _, max_rows = self.execute_query("SHOW max_connections;")
        max_conn = int(max_rows[0][0]) if max_rows else 100

        _, current_rows = self.execute_query("SELECT count(*) FROM pg_stat_activity;")
        current_conn = int(current_rows[0][0]) if current_rows else 0

        ratio = (current_conn / max_conn) * 100
        self.lbl_curr_val.configure(text=f"{current_conn} 개")
        self.lbl_max_val.configure(text=f"{max_conn} 개")
        self.lbl_ratio_val.configure(text=f"{ratio:.1f} %")

        if ratio >= 85:
            self.lbl_ratio_val.configure(text_color="red")
        elif ratio >= 60:
            self.lbl_ratio_val.configure(text_color="orange")
        else:
            self.lbl_ratio_val.configure(text_color="green")

        self.load_current_menu_data()

    def load_current_menu_data(self):
        if self.current_menu == "disk":
            self.load_disk_usage()
        elif self.current_menu == "slow":
            self.load_slow_queries()
        elif self.current_menu == "seq":
            self.load_sequential_scans()
        elif self.current_menu == "index":
            self.load_unused_indexes()
        elif self.current_menu == "dead":
            self.load_dead_tuples()
        elif self.current_menu == "idx_cache":
            self.load_index_cache()
        elif self.current_menu == "idx_bloat":
            self.load_index_bloat()
        elif self.current_menu == "idx_progress":
            self.load_index_progress()

    def sort_column(self, col):
        reverse = not self.sort_reverse.get(col, False)
        self.sort_reverse[col] = reverse
        data_list = [(self.tree.set(k, col), k) for k in self.tree.get_children("")]

        def convert_sort_key(val_str):
            if not val_str or val_str.strip() == "":
                return -1
            size_match = re.match(r"^([\d.]+)\s*(bytes|kB|MB|GB|TB)$", val_str.strip())
            if size_match:
                num = float(size_match.group(1))
                unit = size_match.group(2)
                multiplier = {"bytes": 1, "kB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4}
                return num * multiplier.get(unit, 1)
            try:
                clean_num = val_str.replace(",", "").strip()
                return float(clean_num)
            except ValueError:
                return val_str.lower()

        data_list.sort(key=lambda t: convert_sort_key(t[0]), reverse=reverse)
        for index, (val, k) in enumerate(data_list):
            self.tree.move(k, "", index)

    def format_value(self, item):
        if item is None:
            return ""
        if isinstance(item, int):
            return f"{item:,}"
        elif isinstance(item, float):
            return f"{item:,.2f}"
        item_str = str(item).strip()
        if item_str.isdigit():
            return f"{int(item_str):,}"
        return item_str

    def update_grid(self, columns, rows, menu_key=None):
        for item in self.tree.get_children():
            self.tree.delete(item)

        self.detail_text.delete("1.0", tk.END)
        self.sort_reverse = {}

        if menu_key and menu_key in self.column_guides:
            self.guide_text_label.configure(text=self.column_guides[menu_key])

        if not rows:
            self.status_label.configure(
                text="조회 상태: 대상 데이터가 없습니다.", text_color="cyan"
            )
            return

        self.tree["show"] = "headings"
        self.tree["columns"] = columns

        for col in columns:
            self.tree.heading(col, text=col, anchor="w", command=lambda c=col: self.sort_column(c))
            self.tree.column(col, width=120, anchor="w")

        if columns:
            self.tree.column(columns[-1], width=400)

        for row in rows:
            processed_row = [self.format_value(item) for item in row]
            self.tree.insert("", "end", values=processed_row)

    def on_grid_select(self, event):
        selected_item = self.tree.selection()
        if not selected_item:
            return
        row_values = self.tree.item(selected_item[0], "values")
        self.detail_text.delete("1.0", tk.END)

        if row_values:
            detail_info = "[전체 행 데이터]\n"
            cols = list(self.tree["columns"])
            for col, val in zip(cols, row_values):
                detail_info += f"■ {col}: {val}\n"
            self.detail_text.insert("1.0", detail_info)

    def menu_switch_trigger(self, menu_name):
        self.current_menu = menu_name

    def click_disk_usage(self):
        self.menu_switch_trigger("disk")
        self.load_disk_usage()

    def click_slow_queries(self):
        self.menu_switch_trigger("slow")
        self.load_slow_queries()

    def click_sequential_scans(self):
        self.menu_switch_trigger("seq")
        self.load_sequential_scans()

    def click_unused_indexes(self):
        self.menu_switch_trigger("index")
        self.load_unused_indexes()

    def click_dead_tuples(self):
        self.menu_switch_trigger("dead")
        self.load_dead_tuples()

    def click_index_cache(self):
        self.menu_switch_trigger("idx_cache")
        self.load_index_cache()

    def click_index_bloat(self):
        self.menu_switch_trigger("idx_bloat")
        self.load_index_bloat()

    def click_index_progress(self):
        self.menu_switch_trigger("idx_progress")
        self.load_index_progress()

    # ------------------ 기본 통계 분석 로직 ------------------
    def load_disk_usage(self):
        self.view_title.configure(text="테이블 및 인덱스별 디스크 사용량 통계")
        query = """
            SELECT schemaname as schema, relname as table_name,
                    pg_size_pretty(pg_relation_size(relid)) as data_size,
                    pg_size_pretty(pg_total_relation_size(relid) - pg_relation_size(relid)) as index_size,
                    pg_size_pretty(pg_total_relation_size(relid)) as total_size,
                    n_live_tup as row_count
            FROM pg_stat_user_tables ORDER BY pg_total_relation_size(relid) DESC LIMIT 50;
        """
        cols, rows = self.execute_query(query)
        self.update_grid(cols, rows, menu_key="disk")

    def load_slow_queries(self):
        self.view_title.configure(text="총 실행시간 상위 10개 쿼리")
        query = """
            SELECT round(total_exec_time::numeric, 2) as total_time_ms, calls,
                    round((total_exec_time / calls)::numeric, 2) as avg_time_ms,
                    round(rows::numeric, 0) as total_rows, query
            FROM pg_stat_statements WHERE query NOT LIKE '%%pg_stat_statements%%'
            ORDER BY total_exec_time DESC LIMIT 10;
        """
        cols, rows = self.execute_query(query)
        self.update_grid(cols, rows, menu_key="slow")

    def load_sequential_scans(self):
        self.view_title.configure(text="순차 스캔(Sequential Scan)이 빈번한 테이블")
        query = """
            SELECT schemaname, relname as table_name, seq_scan, seq_tup_read, idx_scan,
                    CASE WHEN seq_scan > 0 THEN round((seq_tup_read / seq_scan)::numeric, 2) ELSE 0 END as avg_rows_per_scan
            FROM pg_stat_user_tables ORDER BY seq_scan DESC LIMIT 20;
        """
        cols, rows = self.execute_query(query)
        self.update_grid(cols, rows, menu_key="seq")

    def load_unused_indexes(self):
        self.view_title.configure(text="사용률이 저조한(미사용) 인덱스 목록")
        query = """
            SELECT schemaname, relname as table_name, indexrelname as index_name, idx_scan,
                    pg_size_pretty(pg_relation_size(indexrelid)) as index_size
            FROM pg_stat_user_indexes WHERE idx_scan = 0 AND idx_scan IS NOT NULL
            ORDER BY pg_relation_size(indexrelid) DESC;
        """
        cols, rows = self.execute_query(query)
        self.update_grid(cols, rows, menu_key="index")

    def load_dead_tuples(self):
        self.view_title.configure(text="Dead Tuples 비중이 높은 테이블")
        query = """
            SELECT schemaname, relname as table_name, n_dead_tup, n_live_tup,
                   round((n_dead_tup::numeric / (n_live_tup + n_dead_tup + 1)::numeric) * 100, 2) as dead_tuple_ratio_pct,
                    last_vacuum, last_autovacuum
            FROM pg_stat_user_tables ORDER BY n_dead_tup DESC;
        """
        cols, rows = self.execute_query(query)
        self.update_grid(cols, rows, menu_key="dead")

    # ------------------ 신규: 인덱스 정밀 모니터링 로직 ------------------
    def load_index_cache(self):
        self.view_title.configure(text="인덱스 메모리 캐시 히트율(Cache Hit Ratio)")
        query = """
            SELECT
                schemaname,
                relname as table_name,
                indexrelname as index_name,
                idx_blks_read as disk_read_blocks,
                idx_blks_hit as cache_hit_blocks,
                ROUND((idx_blks_hit::numeric / NULLIF(idx_blks_hit + idx_blks_read, 0)) * 100, 2) as cache_hit_pct
            FROM pg_statio_user_indexes
            WHERE (idx_blks_hit + idx_blks_read) > 0
            ORDER BY idx_blks_read DESC LIMIT 50;
        """
        cols, rows = self.execute_query(query)
        self.update_grid(cols, rows, menu_key="idx_cache")

    def load_index_bloat(self):
        self.view_title.configure(text="인덱스 크기 대비 테이블 비율 (Bloat 단편화 진단용)")
        query = """
            SELECT
                i.schemaname,
                i.relname AS table_name,
                i.indexrelname AS index_name,
                pg_size_pretty(pg_relation_size(i.indexrelid)) AS index_size,
                pg_size_pretty(pg_relation_size(i.relid)) AS table_size,
                ROUND((pg_relation_size(i.indexrelid)::numeric / NULLIF(pg_relation_size(i.relid), 0)) * 100, 2) AS index_ratio_pct
            FROM pg_stat_user_indexes i
            ORDER BY pg_relation_size(i.indexrelid) DESC LIMIT 50;
        """
        cols, rows = self.execute_query(query)
        self.update_grid(cols, rows, menu_key="idx_bloat")

    def load_index_progress(self):
        self.view_title.configure(text="진행 중인 인덱스 생성/재생성(REINDEX) 실시간 현황")
        query = """
            SELECT
                pid,
                phase,
                blocks_total,
                blocks_done,
                ROUND((blocks_done::numeric / NULLIF(blocks_total, 0)) * 100, 2) as progress_pct,
                tuples_total,
                tuples_done
            FROM pg_stat_progress_create_index;
        """
        cols, rows = self.execute_query(query)
        self.update_grid(cols, rows, menu_key="idx_progress")

    # ------------------ 신규: 인덱스 정밀 조절(생성/삭제) Dialog ------------------
    def open_index_manager_dialog(self):
        if not self.conn or self.conn.closed:
            messagebox.showerror("오류", "DB에 먼저 연결해야 합니다.")
            return

        dialog = ctk.CTkToplevel(self)
        dialog.title("인덱스 정밀 조절 (생성 및 삭제)")
        dialog.geometry("550x450")
        dialog.grab_set()

        tabview = ctk.CTkTabview(dialog)
        tabview.pack(fill="both", expand=True, padx=15, pady=15)

        tab_create = tabview.add("인덱스 생성")
        tab_drop = tabview.add("인덱스 삭제")

        # --- Tab 1: 인덱스 생성 ---
        lbl_tbl = ctk.CTkLabel(
            tab_create,
            text="테이블명 (예: public.users):",
            font=ctk.CTkFont(size=12, weight="bold"),
        )
        lbl_tbl.pack(anchor="w", padx=10, pady=(10, 2))
        ent_create_tbl = ctk.CTkEntry(tab_create, placeholder_text="public.table_name")
        ent_create_tbl.pack(fill="x", padx=10, pady=2)

        lbl_idx_name = ctk.CTkLabel(
            tab_create, text="신규 인덱스명:", font=ctk.CTkFont(size=12, weight="bold")
        )
        lbl_idx_name.pack(anchor="w", padx=10, pady=(10, 2))
        ent_create_idx = ctk.CTkEntry(tab_create, placeholder_text="idx_table_column")
        ent_create_idx.pack(fill="x", padx=10, pady=2)

        lbl_cols = ctk.CTkLabel(
            tab_create,
            text="컬럼 목록 (복합 인덱스는 콤마로 구분, 적정 2~4개 권장):",
            font=ctk.CTkFont(size=12, weight="bold"),
        )
        lbl_cols.pack(anchor="w", padx=10, pady=(10, 2))
        ent_create_cols = ctk.CTkEntry(tab_create, placeholder_text="col1, col2")
        ent_create_cols.pack(fill="x", padx=10, pady=2)

        chk_concurrently_var = ctk.BooleanVar(value=True)
        chk_concurrently = ctk.CTkCheckBox(
            tab_create,
            text="CONCURRENTLY 사용 (서비스 락 방지 권장)",
            variable=chk_concurrently_var,
        )
        chk_concurrently.pack(anchor="w", padx=10, pady=15)

        def run_create_index():
            tbl = ent_create_tbl.get().strip()
            idx = ent_create_idx.get().strip()
            cols = ent_create_cols.get().strip()
            use_conc = chk_concurrently_var.get()

            if not tbl or not idx or not cols:
                messagebox.showwarning("경고", " 모든 항목을 입력해야 합니다.")
                return

            conc_str = "CONCURRENTLY " if use_conc else ""
            sql = f"CREATE INDEX {conc_str}{idx} ON {tbl} ({cols});"

            if messagebox.askyesno("확인", f"다음 DDL을 실행하시겠습니까?\n\n{sql}"):
                _, _ = self.execute_query(sql)
                messagebox.showinfo(
                    "완료",
                    "인덱스 생성 명령이 전송되었습니다.\n(CONCURRENTLY 적용 시 용량에 따라 다소 시간이 걸릴 수 있습니다.)",
                )
                dialog.destroy()
                self.load_current_menu_data()

        btn_run_create = ctk.CTkButton(
            tab_create,
            text="인덱스 생성 실행",
            fg_color="#2b712b",
            hover_color="#1e4e1e",
            command=run_create_index,
        )
        btn_run_create.pack(fill="x", padx=10, pady=10)

        # --- Tab 2: 인덱스 삭제 ---
        lbl_drop_idx = ctk.CTkLabel(
            tab_drop,
            text="삭제할 인덱스명 (예: public.idx_name):",
            font=ctk.CTkFont(size=12, weight="bold"),
        )
        lbl_drop_idx.pack(anchor="w", padx=10, pady=(10, 2))
        ent_drop_idx = ctk.CTkEntry(tab_drop, placeholder_text="public.idx_name")
        ent_drop_idx.pack(fill="x", padx=10, pady=2)

        chk_drop_concurrently_var = ctk.BooleanVar(value=True)
        chk_drop_concurrently = ctk.CTkCheckBox(
            tab_drop,
            text="CONCURRENTLY 사용 (서비스 락 방지 권장)",
            variable=chk_drop_concurrently_var,
        )
        chk_drop_concurrently.pack(anchor="w", padx=10, pady=15)

        def run_drop_index():
            idx = ent_drop_idx.get().strip()
            use_conc = chk_drop_concurrently_var.get()

            if not idx:
                messagebox.showwarning("경고", "삭제할 인덱스명을 입력하세요.")
                return

            conc_str = "CONCURRENTLY " if use_conc else ""
            sql = f"DROP INDEX {conc_str}{idx};"

            if messagebox.askyesno(
                "경고",
                f"⚠️ 인덱스 삭제는 데이터베이스 조회 성능에 큰 영향을 줄 수 있습니다.\n정말 삭제하시겠습니까?\n\n{sql}",
            ):
                _, _ = self.execute_query(sql)
                messagebox.showinfo("완료", "인덱스 삭제 명령이 완료되었습니다.")
                dialog.destroy()
                self.load_current_menu_data()

        btn_run_drop = ctk.CTkButton(
            tab_drop,
            text="인덱스 삭제 실행",
            fg_color="#B91C1C",
            hover_color="#7F1D1D",
            command=run_drop_index,
        )
        btn_run_drop.pack(fill="x", padx=10, pady=10)


if __name__ == "__main__":
    app = PGPerformanceDashboard()
    app.mainloop()
