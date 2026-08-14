import base64
import datetime
import json
import os
import queue
import re
import socket
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

import customtkinter as ctk
import psycopg2
from psycopg2.extras import RealDictCursor

# PyInstaller 네이티브 스플래시 연동용 처리
try:
    import pyi_splash
except ImportError:
    pyi_splash = None

# Matplotlib 및 PIL 글로벌 변수 선언 (백그라운드에서 지연 로딩됨)
FigureCanvasTkAgg = None
Figure = None
MaxNLocator = None
Image = None
ImageTk = None

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")
CONFIG_FILE = "pg_config.json"

# 대시보드 커넥션 식별을 위한 고유 상수 정의
MONITORING_APP_NAME = "MY_MONITORING_DASHBOARD"

# 중복 실행 방지 및 포커스 통신을 위한 고유 로컬 포트 정의
SINGLE_INSTANCE_PORT = 63148


def ensure_single_instance():
    """
    중복 실행을 방지하고, 이미 실행 중인 인스턴스가 있다면
    포커스 요청 신호를 보낸 뒤 스플래시 창조차 띄우지 않고 프로세스를 종료합니다.
    """
    global single_instance_socket
    single_instance_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        # 포트 바인딩 시도 (첫 번째 실행)
        single_instance_socket.bind(("127.0.0.1", SINGLE_INSTANCE_PORT))
        single_instance_socket.listen(1)
    except OSError:
        # 바인딩 실패 = 이미 프로그램이 실행 중임
        try:
            # 기존 실행 중인 프로세스에 포커스 이동 신호 전송
            client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            client_socket.connect(("127.0.0.1", SINGLE_INSTANCE_PORT))
            client_socket.sendall(b"FOCUS")
            client_socket.close()
        except Exception:
            pass
        # 스플래시 윈도우 생성 전 즉시 종료
        sys.exit(0)


def start_focus_listener(app):
    """
    기존 프로세스에서 백그라운드 스레드로 실행되어
    후속 실행 요청이 들어오면 창을 최상단으로 올리고 포커스를 이동시킵니다.
    """

    def listen():
        while True:
            try:
                conn, _ = single_instance_socket.accept()
                data = conn.recv(1024)
                if data == b"FOCUS":
                    app.after(0, app.bring_to_focus)
                conn.close()
            except Exception:
                break

    threading.Thread(target=listen, daemon=True).start()


def resource_path(relative_path):
    """PyInstaller 번들 내 리소스 파일의 절대 경로를 반환합니다."""
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)


class SplashScreen(ctk.CTkToplevel):
    """단일 600x410 배경 위에 하단 컨트롤을 오버레이하는 Zero-Flicker 스플래시 윈도우"""

    def __init__(self, parent, image_path="splash.png"):
        super().__init__(parent)
        self.parent = parent

        self.overrideredirect(True)
        self.attributes("-topmost", True)

        self.width = 600
        self.height = 410

        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        x = (screen_w - self.width) // 2
        y = (screen_h - self.height) // 2
        self.geometry(f"{self.width}x{self.height}+{x}+{y}")

        global Image, ImageTk
        if Image is None or ImageTk is None:
            from PIL import Image as PILImage
            from PIL import ImageTk as PILImageTk

            Image = PILImage
            ImageTk = PILImageTk

        self.canvas = ctk.CTkCanvas(self, width=self.width, height=self.height, highlightthickness=0, bg="#18191A")
        self.canvas.pack(fill="both", expand=True)

        img_full_path = str(resource_path(image_path))
        if os.path.exists(img_full_path):
            pil_img = Image.open(img_full_path).resize((self.width, self.height), Image.Resampling.LANCZOS)
            self.bg_photo = ImageTk.PhotoImage(pil_img)
            self.canvas.create_image(0, 0, image=self.bg_photo, anchor="nw")
        else:
            self.bg_photo = None

        self.bottom_overlay = ctk.CTkFrame(self.canvas, fg_color="#18191A", height=60, corner_radius=0)
        self.bottom_overlay.pack_propagate(False)

        self.canvas.create_window(
            0,
            self.height - 60,
            window=self.bottom_overlay,
            anchor="nw",
            width=self.width,
            height=60,
        )

        self.lbl_status = ctk.CTkLabel(
            self.bottom_overlay,
            text="Initializing application...",
            font=ctk.CTkFont(size=11),
            text_color="#00d2ff",
            fg_color="transparent",
        )
        self.lbl_status.pack(pady=(8, 2))

        self.progress_bar = ctk.CTkProgressBar(
            self.bottom_overlay,
            width=self.width - 60,
            height=6,
            corner_radius=3,
            progress_color="#00d2ff",
            fg_color="#1b2a4a",
        )
        self.progress_bar.pack(pady=(0, 8))
        self.progress_bar.set(0.0)

    def update_progress(self, value, text):
        """진행률(0.0 ~ 1.0) 및 안내 문구 실시간 업데이트"""
        self.progress_bar.set(value)
        self.lbl_status.configure(text=text)
        self.update_idletasks()


class SessionActionPopup(ctk.CTkToplevel):
    """세션 브라우저 더블클릭 시 생성되는 제어 팝업"""

    def __init__(self, parent, conn, pid, query_text):
        super().__init__(parent)
        self.parent_dashboard = parent
        self.conn = conn
        self.pid = pid
        self.query_text = query_text

        self.title(f"⚡ 세션 분석 및 제어 (PID: {self.pid})")
        self.geometry("900x650")
        self.transient(parent)
        self.grab_set()

        lbl_query = ctk.CTkLabel(
            self,
            text=f"📜 현재 실행 중인 활성 쿼리 (PID: {self.pid})",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#1F6AA5",
        )
        lbl_query.pack(pady=(10, 2), anchor="w", padx=15)

        q_frame = ctk.CTkFrame(self, height=120)
        q_frame.pack(fill="x", padx=15, pady=2)
        q_frame.pack_propagate(False)

        self.txt_query = tk.Text(q_frame, bg="#1e1e1e", fg="#ffffff", insertbackground="white", font=("Consolas", 10))
        self.txt_query.pack(side="left", fill="both", expand=True)
        self.txt_query.insert(tk.END, self.query_text if self.query_text else "실행 중인 쿼리가 없습니다.")
        self.txt_query.configure(state="disabled")

        q_scr = ctk.CTkScrollbar(q_frame, command=self.txt_query.yview)
        q_scr.pack(side="right", fill="y")
        self.txt_query.configure(yscrollcommand=q_scr.set)

        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill="x", padx=15, pady=10)

        self.btn_explain = ctk.CTkButton(
            btn_frame,
            text="🔍 실행 계획 분석 (EXPLAIN Plan)",
            fg_color="#1F6AA5",
            hover_color="#144A75",
            command=self.run_explain,
        )
        self.btn_explain.pack(side="left", expand=True, padx=5)

        self.btn_kill = ctk.CTkButton(
            btn_frame,
            text="💥 세션 강제 종료 (Kill Session)",
            fg_color="#D62728",
            hover_color="#A51D1D",
            command=self.kill_session,
        )
        self.btn_kill.pack(side="left", expand=True, padx=5)

        self.btn_close = ctk.CTkButton(
            btn_frame,
            text="🚪 닫기 (Close)",
            fg_color="#555555",
            hover_color="#444444",
            command=self.destroy,
        )
        self.btn_close.pack(side="left", expand=True, padx=5)

        lbl_plan = ctk.CTkLabel(
            self,
            text="📊 쿼리 실행 계획 (EXPLAIN 안전 분석 결과)",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#2CA02C",
        )
        lbl_plan.pack(pady=(10, 2), anchor="w", padx=15)

        plan_frame = ctk.CTkFrame(self)
        plan_frame.pack(fill="both", expand=True, padx=15, pady=(2, 15))

        self.txt_plan = tk.Text(plan_frame, bg="#202020", fg="#2CA02C", insertbackground="white", font=("Consolas", 10))
        self.txt_plan.pack(side="left", fill="both", expand=True)

        plan_scr = ctk.CTkScrollbar(plan_frame, command=self.txt_plan.yview)
        plan_scr.pack(side="right", fill="y")
        self.txt_plan.configure(yscrollcommand=plan_scr.set)

    def run_explain(self):
        self.txt_plan.configure(state="normal")
        self.txt_plan.delete("1.0", tk.END)

        if self.conn is None or self.conn.closed:
            self.txt_plan.insert(tk.END, "데이터베이스 연결이 해제되었거나 세션이 동기화되지 않았습니다.")
            self.txt_plan.configure(state="disabled")
            return

        if not self.query_text:
            self.txt_plan.insert(tk.END, "실행 중인 유효한 SQL 쿼리가 없습니다.")
            self.txt_plan.configure(state="disabled")
            return

        # 주석 제거 및 순수 쿼리 추출
        q_clean = re.sub(r"--.*?\n", "\n", self.query_text)
        q_clean = re.sub(r"/\*.*?\*/", "", q_clean, flags=re.DOTALL).strip()
        q_upper = q_clean.upper()

        start_keywords = ["SELECT", "INSERT", "UPDATE", "DELETE", "MERGE", "WITH"]
        start_idx = -1

        for kw in start_keywords:
            idx = q_upper.find(kw)
            if idx != -1:
                if start_idx == -1 or idx < start_idx:
                    start_idx = idx

        if start_idx != -1:
            q_clean_check = q_clean[start_idx:].strip()
            q_upper = q_clean_check.upper()
        else:
            q_clean_check = q_clean

        utility_commands = (
            "SHOW",
            "SET",
            "BEGIN",
            "COMMIT",
            "ROLLBACK",
            "ABORT",
            "ALTER",
            "CREATE",
            "DROP",
            "GRANT",
            "REVOKE",
        )
        if q_upper.startswith(utility_commands):
            self.txt_plan.insert(
                tk.END,
                f"💡 [안내] '{q_clean_check.split()[0]}' 문장입니다.\n"
                f"이 명령어는 환경 설정 및 트랜잭션 제어용 유틸리티 명령이므로\n"
                f"실행 계획 분석 대상이 아닙니다.",
            )
            self.txt_plan.configure(state="disabled")
            return

        if q_upper.startswith("BACKGROUND") or "PG_CATALOG." in q_upper or "PG_TYPE" in q_upper or "CURRENT_SCHEMAS" in q_upper:
            self.txt_plan.insert(
                tk.END,
                "💡 [안내] 내부 시스템 카탈로그 조회 쿼리이므로 실행 계획 분석 대상이 아닙니다.",
            )
            self.txt_plan.configure(state="disabled")
            return

        if "$1" in q_clean_check or "$" in q_clean_check:
            self.txt_plan.insert(
                tk.END,
                "💡 [안내] 프리페어드 스테이트먼트(Prepared Statement) 쿼리입니다.\n"
                "바인딩 변수 값이 없는 상태이므로 실행 계획을 추출할 수 없습니다.\n\n"
                "Target SQL:\n" + q_clean_check,
            )
            self.txt_plan.configure(state="disabled")
            return

        try:
            with self.conn.cursor() as cur:
                clean_q = q_clean_check.split(";")[0]
                if not clean_q.strip():
                    self.txt_plan.insert(tk.END, "실행 중인 유효한 SQL 쿼리가 없습니다.")
                    self.txt_plan.configure(state="disabled")
                    return
                cur.execute("SET local statement_timeout = 500;")
                cur.execute(f"EXPLAIN (BUFFERS) {clean_q}")
                plans = cur.fetchall()
                for line in plans:
                    self.txt_plan.insert(tk.END, line[0] + "\n")
        except Exception as e:
            try:
                self.conn.rollback()
            except Exception:
                pass
            self.txt_plan.insert(tk.END, f"❌ 실행 계획 추출 실패.\n이유: {e}\n\nTarget SQL:\n{q_clean_check}")

        self.txt_plan.configure(state="disabled")

    def kill_session(self):
        if not self.conn or self.conn.closed:
            return
        if messagebox.askyesno("세션 강제 종료", f"PID {self.pid} 프로세스를 강제 종료하시겠습니까?"):
            self.parent_dashboard.async_kill_session(self.pid)
            self.destroy()


class LockTreePopup(ctk.CTkToplevel):
    """트리 구조 및 세션 킬 메뉴가 내장된 계층형 독점 락 트리 모달 팝업"""

    def __init__(self, parent, conn):
        super().__init__(parent)
        self.parent_dashboard = parent
        self.conn = conn
        self.title("🔒 PostgreSQL Lock Tree Structure")
        self.geometry("900x420")

        self.transient(parent)
        self.grab_set()

        lbl = ctk.CTkLabel(
            self,
            text="🔒 경합 중인 락 계층 구조 (우클릭: 세션 강제 종료)",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color="#D62728",
        )
        lbl.pack(pady=10)

        frame = ctk.CTkFrame(self)
        frame.pack(fill="both", expand=True, padx=15, pady=5)

        scroll = ctk.CTkScrollbar(frame)
        scroll.pack(side="right", fill="y")

        cols = ("pid", "blocked_by", "usename", "state", "query")
        self.tree = ttk.Treeview(frame, columns=cols, show="tree headings", yscrollcommand=scroll.set)
        scroll.configure(command=self.tree.yview)

        self.tree.heading("#0", text="Lock Hierarchy")
        self.tree.heading("pid", text="PID")
        self.tree.heading("blocked_by", text="Blocked By")
        self.tree.heading("usename", text="User")
        self.tree.heading("state", text="State")
        self.tree.heading("query", text="Query")

        self.tree.column("#0", width=180, anchor="w")
        self.tree.column("pid", width=70, anchor="center")
        self.tree.column("blocked_by", width=80, anchor="center")
        self.tree.column("usename", width=90, anchor="center")
        self.tree.column("state", width=80, anchor="center")
        self.tree.column("query", width=400, anchor="w")

        self.tree.pack(fill="both", expand=True)

        self.popup_menu = tk.Menu(self, tearoff=0, background="#2b2b2b", foreground="white", activebackground="#D62728")
        self.popup_menu.add_command(label="💥 블로킹/대기 세션 종료 (Kill Session)", command=self.kill_lock_session)
        self.tree.bind("<Button-3>", self.show_lock_menu)

        self.load_tree(conn)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def show_lock_menu(self, event):
        row = self.tree.identify_row(event.y)
        if row:
            self.tree.selection_set(row)
            self.popup_menu.post(event.x_root, event.y_root)

    def kill_lock_session(self):
        if not self.conn or self.conn.closed:
            return

        selected_item = self.tree.selection()
        if not selected_item:
            return

        item_data = self.tree.item(selected_item[0])
        pid = item_data["values"][0]

        if pid in ("-", "ROOT"):
            messagebox.showwarning("경고", "유효한 프로세스 PID가 아닙니다.")
            return

        if messagebox.askyesno("락 세션 종료", f"락 경합과 관련된 PID {pid} 세션을 강제 종료하시겠습니까?"):
            self.parent_dashboard.async_kill_session(pid)
            self.after(500, lambda: self.tree.delete(*self.tree.get_children()))
            self.after(600, lambda: self.load_tree(self.conn))

    def load_tree(self, conn):
        """pg_blocking_pids() 기반 계층형 락 트리 생성 로직"""
        if conn is None or conn.closed:
            return
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    WITH RECURSIVE lock_graph AS (
                        SELECT
                            a.pid AS waiting_pid,
                            NULL::integer AS blocking_pid,
                            1 AS level,
                            ARRAY[a.pid] AS path
                        FROM pg_stat_activity a
                        WHERE a.pid IN (
                            SELECT unnest(pg_blocking_pids(pid)) FROM pg_stat_activity
                        )
                        AND cardinality(pg_blocking_pids(a.pid)) = 0

                        UNION ALL

                        SELECT
                            a.pid AS waiting_pid,
                            (pg_blocking_pids(a.pid))[1] AS blocking_pid,
                            lg.level + 1,
                            lg.path || a.pid
                        FROM pg_stat_activity a
                        JOIN lock_graph lg ON lg.waiting_pid = (pg_blocking_pids(a.pid))[1]
                        WHERE NOT (a.pid = ANY(lg.path))
                    )
                    SELECT
                        lg.waiting_pid,
                        lg.blocking_pid,
                        lg.level,
                        a.usename,
                        a.state,
                        a.query
                    FROM lock_graph lg
                    JOIN pg_stat_activity a ON lg.waiting_pid = a.pid
                    ORDER BY lg.path;
                """)
                rows = cur.fetchall()
                if not rows:
                    self.tree.insert("", "end", text="✔ 락 경합 없음", values=("-", "-", "-", "-", "-"))
                    return

                inserted_nodes = {}
                for r in rows:
                    pid = r["waiting_pid"]
                    parent_pid = r["blocking_pid"]

                    parent_node = ""
                    if parent_pid in inserted_nodes:
                        parent_node = inserted_nodes[parent_pid]
                    elif parent_pid and parent_pid != "-":
                        root_id = self.tree.insert(
                            "",
                            "end",
                            text=f"⛔ Blocker {parent_pid}",
                            open=True,
                            values=(
                                parent_pid,
                                "ROOT",
                                "시스템",
                                "active",
                                "Lock Holder Main Context",
                            ),
                        )
                        inserted_nodes[parent_pid] = root_id
                        parent_node = root_id

                    q_clean = " ".join(str(r["query"]).split()) if r["query"] else ""
                    node_id = self.tree.insert(
                        parent_node,
                        "end",
                        text=f"⚠ Waiting {pid}",
                        open=True,
                        values=(
                            pid,
                            parent_pid if parent_pid else "ROOT",
                            r["usename"],
                            r["state"],
                            q_clean,
                        ),
                    )
                    inserted_nodes[pid] = node_id
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass

    def on_close(self):
        self.parent_dashboard.lock_tree_window = None
        self.destroy()


class PostgresDashboard(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.withdraw()

        self.title("🐘 PostgreSQL Advanced Tuning Dashboard (Prod Emergency Fix)")
        self.geometry("1450x940")
        self.protocol("WM_DELETE_WINDOW", self.on_exit)

        self.splash = SplashScreen(self, image_path="splash.png")
        self.splash.lift()
        self.splash.attributes("-topmost", True)
        self.splash.update_idletasks()
        self.splash.update()

        self.after(80, self._safe_close_pyi_splash)

        self.conn = None
        self.is_connected = False
        self.lock_tree_window = None

        self.refresh_interval = 0
        self.timer_id = None

        self.metrics_queue = queue.Queue()
        self.is_fetching = False

        self.disk_fetch_counter = 0
        self.cached_db_size = "Unknown"
        self.cached_max_conns = 100
        self.cached_log_path = "Disabled"

        self.job_start_times = {}

        self.max_points = 20
        self.time_data = [""] * self.max_points

        self.ash_cpu = [0] * self.max_points
        self.ash_lock = [0] * self.max_points
        self.ash_io = [0] * self.max_points
        self.ash_idle = [0] * self.max_points

        threading.Thread(target=self.init_app_async, daemon=True).start()

    def bring_to_focus(self):
        """중복 실행 시 기존 창을 최상단으로 복원하고 포커스를 가져옵니다."""
        if self.state() == "iconic":
            self.deiconify()
        self.lift()
        self.attributes("-topmost", True)
        self.attributes("-topmost", False)
        self.focus_force()

    def _safe_close_pyi_splash(self):
        """OS 화면 렌더링 안착 후 PyInstaller 네이티브 스플래시 파기"""
        if pyi_splash and pyi_splash.is_alive():
            try:
                pyi_splash.close()
            except Exception:
                pass

    def update_splash_progress(self, progress, text):
        self.after(0, lambda: self.splash.update_progress(progress, text))

    def init_app_async(self):
        """백그라운드 모듈 로딩 및 GUI 단계적 동기화 초기화"""
        self.update_splash_progress(0.15, "Importing rendering engine...")
        global FigureCanvasTkAgg, Figure, MaxNLocator
        import matplotlib

        matplotlib.use("TkAgg")
        from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg as FCTkAgg
        from matplotlib.figure import Figure as Fig
        from matplotlib.ticker import MaxNLocator as MNL

        FigureCanvasTkAgg = FCTkAgg
        Figure = Fig
        MaxNLocator = MNL

        steps = [
            (0.35, "Database initialize: Panel", self.create_db_conn_panel),
            (0.55, "Database initialize: Layout", self.create_main_layout),
            (0.75, "Database initialize: Charts", self.setup_charts),
            (0.88, "Database initialize: Theme", self.setup_theme_treeview),
            (0.95, "Database initialize: Config", self.load_saved_config),
        ]

        for progress, msg, func in steps:
            self.update_splash_progress(progress, msg)

            done_event = threading.Event()

            def run_gui_task():
                func()
                if self.splash and self.splash.winfo_exists():
                    self.splash.update_idletasks()
                done_event.set()

            self.after(0, run_gui_task)
            done_event.wait()
            time.sleep(0.08)

        self.update_splash_progress(1.0, "Database initialize: 100%")
        self.after(100, self.check_queue_loop)
        self.after(200, self.finish_loading)

    def finish_loading(self):
        """메인 창 가시화 후 스플래시 안전 제거"""
        self.deiconify()

        self.update_idletasks()
        self.update()

        if hasattr(self, "splash") and self.splash is not None:
            self.splash.destroy()
            self.splash = None

        self.focus_force()

    def create_db_conn_panel(self):
        self.conn_frame = ctk.CTkFrame(self, height=60)
        self.conn_frame.pack(side="top", fill="x", padx=10, pady=5)

        self.entry_host = ctk.CTkEntry(self.conn_frame, placeholder_text="Host", width=120)
        self.entry_host.pack(side="left", padx=4, pady=10)
        self.entry_port = ctk.CTkEntry(self.conn_frame, placeholder_text="Port", width=60)
        self.entry_port.pack(side="left", padx=4, pady=10)
        self.entry_port.insert(0, "5432")
        self.entry_db = ctk.CTkEntry(self.conn_frame, placeholder_text="Database", width=110)
        self.entry_db.pack(side="left", padx=4, pady=10)
        self.entry_user = ctk.CTkEntry(self.conn_frame, placeholder_text="User", width=100)
        self.entry_user.pack(side="left", padx=4, pady=10)
        self.entry_pass = ctk.CTkEntry(self.conn_frame, placeholder_text="Password", show="*", width=100)
        self.entry_pass.pack(side="left", padx=4, pady=10)

        self.var_save_info = ctk.BooleanVar(value=False)
        self.chk_save_info = ctk.CTkCheckBox(
            self.conn_frame,
            text="정보 저장",
            variable=self.var_save_info,
            width=80,
        )
        self.chk_save_info.pack(side="left", padx=4, pady=10)

        self.btn_connect = ctk.CTkButton(
            self.conn_frame,
            text="연결 (Connect)",
            width=110,
            command=self.toggle_connection,
        )
        self.btn_connect.pack(side="left", padx=6, pady=10)

        lbl_trig = ctk.CTkLabel(self.conn_frame, text="⏱ 주기:", font=ctk.CTkFont(size=11))
        lbl_trig.pack(side="left", padx=(10, 2), pady=10)

        self.combo_trigger = ctk.CTkComboBox(
            self.conn_frame,
            values=["수동", "1초", "3초", "5초", "10초"],
            width=75,
            command=self.change_trigger_interval,
        )
        self.combo_trigger.pack(side="left", padx=2, pady=10)
        self.combo_trigger.set("수동")

        self.btn_refresh = ctk.CTkButton(
            self.conn_frame,
            text="🔄 Refresh",
            width=80,
            fg_color="#2CA02C",
            hover_color="#218023",
            command=self.manual_refresh_dashboard,
        )
        self.btn_refresh.pack(side="left", padx=4, pady=10)

        self.lbl_status = ctk.CTkLabel(
            self.conn_frame,
            text="Disconnected",
            text_color="#D62728",
            font=ctk.CTkFont(weight="bold"),
        )
        self.lbl_status.pack(side="right", padx=15, pady=10)

    def create_main_layout(self):
        self.top_container = ctk.CTkFrame(self)
        self.top_container.pack(side="top", fill="both", expand=True)

        self.chart_frame = ctk.CTkFrame(self.top_container)
        self.chart_frame.pack(side="left", fill="both", expand=True, padx=10, pady=5)

        self.card_frame = ctk.CTkScrollableFrame(self.top_container, width=295)
        self.card_frame.pack(side="right", fill="both", expand=False, padx=10, pady=5)

        lbl_title = ctk.CTkLabel(
            self.card_frame,
            text="성능 대시보드 지표",
            font=ctk.CTkFont(size=15, weight="bold"),
        )
        lbl_title.pack(pady=6)

        self.disk_card = self.create_status_card(
            self.card_frame,
            "💾 Database Disk Size",
            "Current DB Size: -\nStatus: -",
        )
        self.wal_card = self.create_status_card(self.card_frame, "WAL State", "LSN: -\nStatus: -")
        self.lock_card = self.create_status_card(self.card_frame, "ASH Alert Context", "Long Run SQL: 0\nBlockers: 0")
        self.conn_card = self.create_status_card(
            self.card_frame,
            "Connection Saturation",
            "Conn Usage: -\nMax Settings: -",
        )
        self.rollback_card = self.create_status_card(self.card_frame, "Transaction Health", "Rollback Rate: -\nStatus: -")
        self.sys_log_card = self.create_status_card(
            self.card_frame,
            "🔔 Active System Log Path",
            "Log File: -\nStatus: Pending...",
        )

        guide_text = (
            "📌 [Disk Size]\n   300초(5분) 주기 혹은 수동 Refresh 시 스캔하여 물리 자원 보호\n\n"
            "📌 [WAL State]\n   트랜잭션 엔진 안전 가동을 위한 엔진 최신 LSN 로그 파싱 상태\n\n"
            "📌 [ASH Alert]\n   분석용 본인 세션을 완벽 배제한 실시간 순수 앱 부하 스택\n\n"
            "📌 [Saturation]\n   시스템 내부의 물리 커넥션 한도 검사 (80% 도달 시 경고)\n\n"
            "📌 [TX Health]\n   엔진 내의 비정상 처리 롤백 비율 계측 (5% 초과 시 위험)\n\n"
            "📌 [System Log]\n   PostgreSQL 인스턴스가 활성 기록 중인 실시간 물리 로그 경로\n\n"
            "📌 [제어 팝업]\n   더블클릭 연동 500ms 제한 타임아웃 적용 세션 안전 제어"
        )
        self.guide_card = ctk.CTkLabel(
            self.card_frame,
            text=guide_text,
            font=ctk.CTkFont(size=11),
            justify="left",
            anchor="w",
        )
        self.guide_card.pack(anchor="w", padx=(15, 5), pady=8, fill="x")

        self.progress_container = ctk.CTkFrame(self, height=180)
        self.progress_container.pack(side="top", fill="x", padx=10, pady=5)

        progress_ctrl = ctk.CTkFrame(self.progress_container, fg_color="transparent")
        progress_ctrl.pack(fill="x", padx=10, pady=2)

        lbl_progress_title = ctk.CTkLabel(
            progress_ctrl,
            text="⚡ 백그라운드 관리 작업 진행 현황 (인덱스 생성, Vacuum, Analyze, Cluster)",
            font=ctk.CTkFont(size=12, weight="bold"),
        )
        lbl_progress_title.pack(side="left")

        progress_scroll = ctk.CTkScrollbar(self.progress_container)
        progress_scroll.pack(side="right", fill="y")

        progress_cols = (
            "pid",
            "job_type",
            "phase",
            "progress_percent",
            "elapsed_minutes",
        )
        self.progress_tree = ttk.Treeview(
            self.progress_container,
            columns=progress_cols,
            show="headings",
            yscrollcommand=progress_scroll.set,
            height=4,
            selectmode="browse",
        )
        progress_scroll.configure(command=self.progress_tree.yview)

        self.progress_tree.heading("pid", text="PID", anchor="center")
        self.progress_tree.heading("job_type", text="작업 종류", anchor="center")
        self.progress_tree.heading("phase", text="진행 단계", anchor="center")
        self.progress_tree.heading("progress_percent", text="진행률 (%)", anchor="center")
        self.progress_tree.heading("elapsed_minutes", text="경과 시간", anchor="center")

        self.progress_tree.column("pid", width=90, anchor="center")
        self.progress_tree.column("job_type", width=180, anchor="center")
        self.progress_tree.column("phase", width=450, anchor="w")
        self.progress_tree.column("progress_percent", width=160, anchor="center")
        self.progress_tree.column("elapsed_minutes", width=150, anchor="center")
        self.progress_tree.pack(fill="both", expand=True, padx=10, pady=5)

        self.session_container = ctk.CTkFrame(self, height=280)
        self.session_container.pack(side="bottom", fill="both", expand=True, padx=10, pady=5)

        ctrl = ctk.CTkFrame(self.session_container, fg_color="transparent")
        ctrl.pack(fill="x", padx=10, pady=2)

        lbl_s = ctk.CTkLabel(
            ctrl,
            text="📊 Live Session Browser (항목 더블클릭 시 상세 제어 및 안전 실행계획 확인 가능)",
            font=ctk.CTkFont(size=13, weight="bold"),
        )
        lbl_s.pack(side="left")

        self.btn_lock_tree = ctk.CTkButton(ctrl, text="🔒 Lock Tree", width=100, command=self.open_lock_tree)
        self.btn_lock_tree.pack(side="right", padx=5)

        scroll = ctk.CTkScrollbar(self.session_container)
        scroll.pack(side="right", fill="y")

        cols = (
            "pid",
            "usename",
            "application_name",
            "client_addr",
            "state",
            "duration",
            "wait_event",
            "query",
        )

        self.session_tree = ttk.Treeview(
            self.session_container,
            columns=cols,
            show="headings",
            yscrollcommand=scroll.set,
            height=8,
            selectmode="browse",
        )
        scroll.configure(command=self.session_tree.yview)

        self.session_tree.heading("pid", text="PID", anchor="center", command=lambda: self.sort_column("pid", False))
        self.session_tree.heading(
            "usename",
            text="User",
            anchor="center",
            command=lambda: self.sort_column("usename", False),
        )
        self.session_tree.heading(
            "application_name",
            text="App",
            anchor="center",
            command=lambda: self.sort_column("application_name", False),
        )
        self.session_tree.heading(
            "client_addr",
            text="IP",
            anchor="center",
            command=lambda: self.sort_column("client_addr", False),
        )
        self.session_tree.heading("state", text="State", anchor="center", command=lambda: self.sort_column("state", False))
        self.session_tree.heading(
            "duration",
            text="Duration",
            anchor="center",
            command=lambda: self.sort_column("duration", False),
        )
        self.session_tree.heading(
            "wait_event",
            text="Wait Event",
            anchor="center",
            command=lambda: self.sort_column("wait_event", False),
        )
        self.session_tree.heading(
            "query",
            text="Active Query Text",
            anchor="w",
            command=lambda: self.sort_column("query", False),
        )

        self.session_tree.column("pid", width=60, anchor="center")
        self.session_tree.column("usename", width=80, anchor="center")
        self.session_tree.column("application_name", width=100, anchor="center")
        self.session_tree.column("client_addr", width=100, anchor="center")
        self.session_tree.column("state", width=80, anchor="center")
        self.session_tree.column("duration", width=85, anchor="center")
        self.session_tree.column("wait_event", width=110, anchor="center")
        self.session_tree.column("query", width=520, anchor="w")
        self.session_tree.pack(fill="both", expand=True, padx=10, pady=5)

        self.session_tree.bind("<Double-1>", self.open_session_popup)

    def sort_column(self, col, reverse):
        l = [(self.session_tree.set(k, col), k) for k in self.session_tree.get_children("")]

        def get_sort_key(item):
            val = item[0]
            try:
                if ":" in val:
                    return val
                return float(val)
            except ValueError:
                return val.lower()

        l.sort(key=get_sort_key, reverse=reverse)

        for index, (val, k) in enumerate(l):
            self.session_tree.move(k, "", index)

        self.session_tree.heading(col, command=lambda: self.sort_column(col, not reverse))

    def open_session_popup(self, event):
        if not self.is_connected:
            return
        row = self.session_tree.identify_row(event.y)
        if row:
            self.session_tree.selection_set(row)
            item = self.session_tree.item(row)
            pid = item["values"][0]
            query_text = item["values"][7]
            SessionActionPopup(self, self.conn, pid, query_text)

    def open_lock_tree(self):
        if not self.is_connected:
            return

        if self.lock_tree_window is not None and self.lock_tree_window.winfo_exists():
            self.lock_tree_window.focus()
            return

        self.lock_tree_window = LockTreePopup(self, self.conn)

    def create_status_card(self, parent, title, text):
        f = ctk.CTkFrame(parent, border_width=1, border_color="#333333")
        f.pack(fill="x", padx=12, pady=4)
        ctk.CTkLabel(
            f,
            text=title,
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#1F6AA5",
        ).pack(anchor="w", padx=10)
        lbl = ctk.CTkLabel(f, text=text, font=ctk.CTkFont(size=11), justify="left")
        lbl.pack(anchor="w", padx=10, pady=3)
        return lbl

    def setup_charts(self):
        global Figure, FigureCanvasTkAgg
        self.fig = Figure(figsize=(10, 2.2), facecolor="#2b2b2b")

        self.ax_ash = self.fig.add_subplot(111)
        self.ax_ash.set_title(
            "1. ASH Active Session History Stack Tracker",
            fontsize=9,
            weight="bold",
            color="#2CA02C",
        )

        self.ax_ash.set_facecolor("#202020")
        self.ax_ash.grid(True, color="#333333", linestyle="--", linewidth=0.5)
        self.ax_ash.tick_params(axis="x", colors="white", rotation=5, labelsize=7)
        self.ax_ash.tick_params(axis="y", colors="white", labelsize=8)

        self.fig.tight_layout()
        self.canvas = FigureCanvasTkAgg(self.fig, master=self.chart_frame)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)

    def toggle_connection(self):
        if not self.is_connected:
            try:
                self.conn = psycopg2.connect(
                    host=self.entry_host.get(),
                    port=int(self.entry_port.get()),
                    database=self.entry_db.get(),
                    user=self.entry_user.get(),
                    password=self.entry_pass.get(),
                    connect_timeout=3,
                    application_name=MONITORING_APP_NAME,
                )
                self.conn.autocommit = True

                with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute("SET statement_timeout = 2000;")

                    cur.execute("SHOW max_connections;")
                    res_max = cur.fetchone()
                    self.cached_max_conns = int(res_max["max_connections"]) if res_max else 100

                    try:
                        cur.execute("SELECT pg_current_logfile() AS current_log;")
                        log_res = cur.fetchone()
                        self.cached_log_path = log_res["current_log"] if log_res and log_res.get("current_log") else "Disabled"
                    except Exception:
                        self.cached_log_path = "Disabled"

                self.is_connected = True
                self.lbl_status.configure(text="Connected", text_color="#2CA02C")
                self.btn_connect.configure(text="연결 해제 (Disconnect)", fg_color="#D62728")
                if self.var_save_info.get():
                    self.save_config()

                self.manual_refresh_dashboard()
                self.schedule_next_refresh()
            except Exception as e:
                messagebox.showerror("접속 오류", f"데이터베이스 연결에 실패했습니다: {e}")
        else:
            self.disconnect_db()

    def disconnect_db(self):
        if self.timer_id is not None:
            self.after_cancel(self.timer_id)
            self.timer_id = None

        if self.conn:
            try:
                self.conn.close()
            except Exception:
                pass
        self.conn = None
        self.is_connected = False
        self.lbl_status.configure(text="Disconnected", text_color="#D62728")
        self.btn_connect.configure(text="연결 (Connect)", fg_color="#1F6AA5")
        self.btn_lock_tree.configure(fg_color="#1F6AA5")

    def save_config(self):
        try:
            raw_password = self.entry_pass.get()
            encoded_bytes = base64.b64encode(raw_password.encode("utf-8"))
            encrypted_password = encoded_bytes.decode("utf-8")

            cfg = {
                "host": self.entry_host.get(),
                "port": self.entry_port.get(),
                "database": self.entry_db.get(),
                "user": self.entry_user.get(),
                "pass_enc": encrypted_password,
                "save_info": True,
            }
            with open(CONFIG_FILE, "w") as f:
                json.dump(cfg, f)
        except Exception:
            pass

    def load_saved_config(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r") as f:
                    c = json.load(f)
                    if c.get("save_info"):
                        self.entry_host.insert(0, c.get("host", ""))
                        self.entry_port.delete(0, tk.END)
                        self.entry_port.insert(0, c.get("port", "5432"))
                        self.entry_db.insert(0, c.get("database", ""))
                        self.entry_user.insert(0, c.get("user", ""))

                        enc_pass = c.get("pass_enc", "")
                        if enc_pass:
                            decoded_bytes = base64.b64decode(enc_pass.encode("utf-8"))
                            decrypted_password = decoded_bytes.decode("utf-8")
                            self.entry_pass.insert(0, decrypted_password)

                        self.var_save_info.set(True)
            except Exception:
                pass

    def manual_refresh_dashboard(self):
        if self.is_connected and not self.is_fetching:
            self.is_fetching = True
            threading.Thread(target=self.async_fetch_worker, args=(True,), daemon=True).start()

    def force_refresh_dashboard(self):
        if self.is_connected and not self.is_fetching:
            self.is_fetching = True
            threading.Thread(target=self.async_fetch_worker, args=(False,), daemon=True).start()

    def async_fetch_worker(self, is_manual=False):
        data = self.fetch_metrics(is_manual)
        if data is not None:
            self.metrics_queue.put(("METRICS", data))

    def async_kill_session(self, pid):
        if not self.is_connected:
            return

        def kill_worker():
            temp_conn = None
            try:
                temp_conn = psycopg2.connect(
                    host=self.entry_host.get(),
                    port=int(self.entry_port.get()),
                    database=self.entry_db.get(),
                    user=self.entry_user.get(),
                    password=self.entry_pass.get(),
                    connect_timeout=3,
                    application_name=MONITORING_APP_NAME,
                )
                temp_conn.autocommit = True
                with temp_conn.cursor() as cur:
                    cur.execute("SELECT pg_terminate_backend(%s);", (int(pid),))
                self.manual_refresh_dashboard()
            except Exception as e:
                self.metrics_queue.put(("ERROR", f"세션 강제 종료 실패: {e}"))
            finally:
                if temp_conn:
                    try:
                        temp_conn.close()
                    except Exception:
                        pass

        threading.Thread(target=kill_worker, daemon=True).start()

    def check_queue_loop(self):
        try:
            while not self.metrics_queue.empty():
                msg_type, payload = self.metrics_queue.get_nowait()
                if msg_type == "METRICS" and payload:
                    self.render_dashboard_ui(payload)
                elif msg_type == "DISCONNECT":
                    self.disconnect_db()
                    messagebox.showerror("연결 오류", payload)
                elif msg_type == "ERROR":
                    messagebox.showerror("Error", payload)
                self.is_fetching = False
        except queue.Empty:
            pass
        finally:
            self.after(200, self.check_queue_loop)

    def fetch_metrics(self, is_manual=False):
        if not self.is_connected or self.conn is None or self.conn.closed:
            return None
        data = {}
        try:
            with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
                if is_manual or (self.disk_fetch_counter % 300 == 0):
                    try:
                        cur.execute("SELECT pg_size_pretty(pg_database_size(current_database())) as db_size;")
                        res_disk = cur.fetchone()
                        if res_disk:
                            self.cached_db_size = res_disk["db_size"]
                    except Exception:
                        pass
                    data["disk_status"] = "Real-time" if is_manual else "Safe Protected"
                else:
                    data["disk_status"] = f"Cached ({self.disk_fetch_counter % 300}/300s)"

                if not is_manual:
                    self.disk_fetch_counter += 1

                data["db_size"] = self.cached_db_size

                cur.execute("""
                    SELECT
                        (SELECT count(*) FROM pg_stat_activity) as total_physical_conns,
                        sum(blks_read) as br,
                        sum(blks_hit) as bh,
                        sum(xact_commit) as commits,
                        sum(xact_rollback) as rollbacks
                    FROM pg_stat_database;
                """)
                res = cur.fetchone()
                if res is not None:
                    data["conns"] = int(res.get("total_physical_conns") or 0)
                    br = float(res.get("br") or 0)
                    bh = float(res.get("bh") or 0)
                    data["hit_ratio"] = (bh / (br + bh) * 100.0) if (br + bh) > 0 else 100.0

                    commits = float(res.get("commits") or 0)
                    rollbacks = float(res.get("rollbacks") or 0)
                    total_xact = commits + rollbacks
                    data["rollback_ratio"] = (rollbacks / total_xact * 100.0) if total_xact > 0 else 0.0
                else:
                    data["conns"], data["hit_ratio"], data["rollback_ratio"] = (0, 100.0, 0.0)

                data["max_conns"] = self.cached_max_conns
                data["sys_log_path"] = self.cached_log_path

                cur.execute("SELECT pg_current_wal_lsn();")
                res_wal = cur.fetchone()
                data["wal"] = res_wal.get("pg_current_wal_lsn", "-") if res_wal is not None else "-"

                cur.execute("""
                    SELECT pid, usename, application_name, client_addr, state, wait_event_type, wait_event, query,
                           query_start, now() - query_start as duration_raw,
                           to_char(coalesce(now() - query_start, interval '0s'), 'HH24:MI:SS') as run_duration
                    FROM pg_stat_activity;
                """)
                all_activity_rows = cur.fetchall() or []

                my_backend_pid = self.conn.get_backend_pid()

                cpu_cnt = 0
                lock_cnt = 0
                io_cnt = 0
                idle_cnt = 0
                long_q_cnt = 0

                dashboard_cnt = 0
                pure_service_cnt = 0
                filtered_sessions = []

                for row in all_activity_rows:
                    pid = row["pid"]
                    state = row["state"]
                    wait_event_type = row["wait_event_type"]
                    app_name = str(row.get("application_name", "")).strip()

                    if app_name == MONITORING_APP_NAME:
                        dashboard_cnt += 1
                    else:
                        pure_service_cnt += 1

                    if pid == my_backend_pid:
                        continue

                    if app_name != MONITORING_APP_NAME:
                        if state == "active":
                            if wait_event_type is None:
                                cpu_cnt += 1
                            elif wait_event_type == "Lock":
                                lock_cnt += 1
                            elif wait_event_type == "IO":
                                io_cnt += 1

                            raw_dur = row["duration_raw"]
                            if raw_dur and raw_dur.total_seconds() > 10:
                                long_q_cnt += 1
                        elif state == "idle":
                            idle_cnt += 1

                    if state != "idle" or wait_event_type is not None:
                        filtered_sessions.append(row)

                data["ash_cpu"] = cpu_cnt
                data["ash_lock"] = lock_cnt
                data["ash_io"] = io_cnt
                data["ash_idle"] = idle_cnt
                data["long_queries"] = long_q_cnt

                data["dashboard_conns"] = dashboard_cnt
                data["pure_service_conns"] = pure_service_cnt
                data["sessions"] = filtered_sessions

                cur.execute("""
                    SELECT p.pid, 'Index Build' AS job_type, p.phase,
                           p.blocks_done, p.blocks_total
                    FROM pg_stat_progress_create_index p
                    UNION ALL
                    SELECT p.pid, 'Analyze (Stats)' AS job_type, p.phase,
                           p.sample_blks_scanned AS blocks_done, p.sample_blks_total AS blocks_total
                    FROM pg_stat_progress_analyze p
                    UNION ALL
                    SELECT p.pid, 'Vacuum' AS job_type, p.phase,
                           p.heap_blks_vacuumed AS blocks_done, p.heap_blks_total AS blocks_total
                    FROM pg_stat_progress_vacuum p;
                """)
                data["progress_jobs"] = cur.fetchall() or []

        except (psycopg2.OperationalError, psycopg2.InterfaceError) as e:
            self.metrics_queue.put(("DISCONNECT", f"데이터베이스 연결이 끊어졌습니다: {e}"))
            return None
        except Exception:
            try:
                self.conn.rollback()
            except Exception:
                pass
            return None
        return data

    def render_dashboard_ui(self, data):
        global MaxNLocator
        curr_time = datetime.datetime.now().strftime("%H:%M:%S")
        self.time_data.pop(0)
        self.time_data.append(curr_time)
        t_idx = [4, 9, 14, 19]
        t_lbl = [self.time_data[i] for i in t_idx]

        self.ash_cpu.pop(0)
        self.ash_cpu.append(data["ash_cpu"])
        self.ash_lock.pop(0)
        self.ash_lock.append(data["ash_lock"])
        self.ash_io.pop(0)
        self.ash_io.append(data["ash_io"])
        self.ash_idle.pop(0)
        self.ash_idle.append(data["ash_idle"])

        if data["ash_lock"] > 0:
            self.btn_lock_tree.configure(fg_color="red")
        else:
            self.btn_lock_tree.configure(fg_color="#1F6AA5")

        self.ax_ash.clear()
        self.ax_ash.set_facecolor("#202020")
        self.ax_ash.grid(True, color="#333333", linestyle="--", linewidth=0.5)
        self.ax_ash.set_title(
            "1. ASH Active Session History Stack Tracker",
            fontsize=9,
            weight="bold",
            color="#2CA02C",
        )

        self.ax_ash.set_ylabel("Active Sessions", color="white", fontsize=8)

        self.ax_ash.stackplot(
            list(range(self.max_points)),
            self.ash_cpu,
            self.ash_lock,
            self.ash_io,
            labels=["CPU", "Lock", "IO"],
            colors=["#2CA02C", "#D62728", "#FF7F0E"],
        )

        if MaxNLocator:
            self.ax_ash.yaxis.set_major_locator(MaxNLocator(integer=True))

        # 실제 시점별 스택 합계 중 최댓값으로 Y축 범위 동적 설정
        stacked_totals = [c + l + i for c, l, i in zip(self.ash_cpu, self.ash_lock, self.ash_io)]
        current_max = max(stacked_totals) if stacked_totals else 0
        self.ax_ash.set_ylim(bottom=0)
        if current_max < 1:
            self.ax_ash.set_ylim(top=1)

        self.ax_ash.set_xticks(t_idx)
        self.ax_ash.set_xticklabels(t_lbl, color="white")
        self.ax_ash.tick_params(axis="y", colors="white")
        self.ax_ash.legend(
            facecolor="#202020",
            edgecolor="none",
            loc="upper left",
            fontsize="x-small",
            framealpha=0.6,
            labelcolor="white",
        )

        status_str = data.get("disk_status", "Safe-Fetched")
        self.disk_card.configure(text=f"Current DB Size: {data['db_size']}\nStatus: {status_str}")

        self.wal_card.configure(text=f"LSN: {data['wal']}\nEngine: Safe Mode Active")
        self.lock_card.configure(text=f"Long Run SQL: {data['long_queries']}\nActive Block Locks: {data['ash_lock']}")

        conn_pct = (data["conns"] / data["max_conns"]) * 100.0 if data["max_conns"] > 0 else 0
        conn_status = "Warning!" if conn_pct > 80 else "Stable"
        self.conn_card.configure(
            text=(
                f"Total Usage: {conn_pct:.1f}% ({data['conns']}/{data['max_conns']})\n"
                f"└ 💻 Pure Service: {data['pure_service_conns']} conns\n"
                f"└ 📊 Dashboard: {data['dashboard_conns']} conns\n"
                f"Status: {conn_status}"
            )
        )

        rb_status = "Critical" if data["rollback_ratio"] > 5.0 else "Healthy"
        self.rollback_card.configure(text=f"Rollback Rate: {data['rollback_ratio']:.2f}%\nStatus: {rb_status}")

        sys_log_path = data.get("sys_log_path", "Disabled")
        if sys_log_path and sys_log_path != "Disabled":
            filename = os.path.basename(sys_log_path)
            self.sys_log_card.configure(text=f"File: {filename}\nPath: {sys_log_path}")
        else:
            self.sys_log_card.configure(text="Log File: Off\nStatus: Logging disabled")

        for item in list(self.progress_tree.get_children()):
            self.progress_tree.delete(item)

        current_pids = set()
        now = datetime.datetime.now()

        for job in data["progress_jobs"]:
            pid = job["pid"]
            current_pids.add(pid)

            blocks_done = job.get("blocks_done") or 0
            blocks_total = job.get("blocks_total") or 0
            phase = job["phase"] if job["phase"] else "-"

            if blocks_total > 0:
                pct = round(100.0 * blocks_done / blocks_total, 2)
                if pct >= 100.0:
                    pct_str = "100% (완료 처리 중)"
                else:
                    pct_str = f"{pct}%"
            else:
                if phase in (
                    "building index: loading tuples in tree",
                    "blocks cleanup",
                    "vacuuming page",
                ):
                    pct_str = "99.9% (최종 정리)"
                else:
                    pct_str = "100% (최종 정리 중)"

            if pid not in self.job_start_times:
                self.job_start_times[pid] = now

            elapsed_td = now - self.job_start_times[pid]
            elapsed_str = str(datetime.timedelta(seconds=int(elapsed_td.total_seconds())))

            self.progress_tree.insert(
                "",
                "end",
                values=(
                    pid,
                    job["job_type"],
                    phase,
                    pct_str,
                    elapsed_str,
                ),
            )

        terminated_pids = set(self.job_start_times.keys()) - current_pids
        for old_pid in terminated_pids:
            self.job_start_times.pop(old_pid, None)

        for item in list(self.session_tree.get_children()):
            self.session_tree.delete(item)

        for row in data["sessions"]:
            q_clean = " ".join(str(row["query"]).split()) if row["query"] else ""
            w_type = row["wait_event_type"]
            w_event = row["wait_event"]
            w_str = f"{w_type}:{w_event}" if w_type else "None"

            self.session_tree.insert(
                "",
                "end",
                values=(
                    row["pid"],
                    row["usename"],
                    row["application_name"],
                    row["client_addr"],
                    row["state"],
                    row["run_duration"],
                    w_str,
                    q_clean,
                ),
            )

        self.canvas.draw_idle()

    def setup_theme_treeview(self):
        s = ttk.Style()
        s.theme_use("clam")

        s.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])

        s.configure(
            "Treeview",
            background="#202020",
            foreground="white",
            rowheight=24,
            fieldbackground="#202020",
            borderwidth=0,
            relief="flat",
        )
        s.map("Treeview", background=[("selected", "#1F6AA5")])
        s.configure(
            "Treeview.Heading",
            background="#2b2b2b",
            foreground="white",
            relief="flat",
            font=("Malgun Gothic", 9, "bold"),
            borderwidth=0,
        )
        s.map("Treeview.Heading", background=[("active", "#383838")])

    def change_trigger_interval(self, value):
        if self.timer_id is not None:
            self.after_cancel(self.timer_id)
            self.timer_id = None

        if value == "수동":
            self.refresh_interval = 0
            self.manual_refresh_dashboard()
        else:
            self.refresh_interval = int(value.replace("초", "")) * 1000
            self.manual_refresh_dashboard()
            self.schedule_next_refresh()

    def schedule_next_refresh(self):
        if self.timer_id is not None:
            self.after_cancel(self.timer_id)
            self.timer_id = None

        if self.is_connected and self.refresh_interval > 0:
            self.force_refresh_dashboard()
            self.timer_id = self.after(self.refresh_interval, self.schedule_next_refresh)

    def on_exit(self):
        if self.timer_id is not None:
            self.after_cancel(self.timer_id)

        self.disconnect_db()

        if hasattr(self, "canvas") and self.canvas:
            self.canvas.get_tk_widget().destroy()

        self.quit()
        self.destroy()
        os._exit(0)


if __name__ == "__main__":
    ensure_single_instance()
    app = PostgresDashboard()
    start_focus_listener(app)
    app.mainloop()
