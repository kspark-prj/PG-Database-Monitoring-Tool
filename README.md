# 🐘 PostgreSQL Advanced Tuning Dashboard

PostgreSQL 데이터베이스의 실시간 상태를 시각적으로 모니터링하고, 경합 중인 세션을 안전하게 제어·분석할 수 있도록 돕는 고성능 모니터링 데스크톱 프로그램입니다.

백그라운드 비동기 처리 구조를 채택하여, DB 조회 지연이나 락 경합 시에도 메인 GUI 화면이 무반응(Freezing) 상태에 빠지지 않고 안정적으로 구동됩니다.

## 🚀 주요 기능 및 핵심 패치 내용

1. **실시간 시스템 로그 경로(Log Path) 노출 극대화**
    - 기존의 단일 예외 처리 루프를 분리하여 `pg_current_logfile()` 조회가 불가능한 환경에서도 `pg_settings` 뷰 및 `data_directory` 정보를 순차적으로 탐색(Fallback)하여 실제 로그 경로를 집요하게 추적, 화면에 출력합니다.
2. **실시간 ASH (Active Session History) Stack Plot 차트**
    - 세션 상태(CPU, Lock, I/O, Idle)를 실시간 누적 영역 그래프로 한눈에 확인할 수 있습니다.
3. **세션 분석 및 안전한 EXPLAIN 분석**
    - `EXPLAIN (BUFFERS)`를 활용해 운영 환경에 부하를 주지 않고 실행 계획을 미리 진단합니다.
4. **계층형 Lock Tree 모달 및 관리 작업 추적**
    - 락 경합 구조 시각화 및 관리 작업(VACUUM, ANALYZE, INDEX)의 진행률/예상 종료 시간을 실시간 모니터링합니다.

## 🛠️ 설치 및 환경 준비

본 프로그램은 `customtkinter`, `matplotlib`, `psycopg2` 라이브러리를 활용합니다.

pip install customtkinter matplotlib psycopg2-binary

> **참고**: 윈도우 환경에서는 `psycopg2-binary`를 설치하는 것이 가장 안정적입니다.

## 💻 실행 방법

### 1. 로컬 환경에서 실행

터미널(또는 명령 프롬프트)을 열고 스크립트가 위치한 폴더로 이동한 뒤 아래 명령어를 입력합니다.

python PG_monitor.py

> **주의**: `.py` 파일을 마우스로 더블클릭하여 실행할 경우, 에러 발생 시 콘솔 창이 즉시 닫혀 원인 파악이 어렵습니다. 초기 설정 단계에서는 **반드시 터미널을 열고 명령어로 실행**하시길 권장합니다.

## 🏗️ 프로그램 구성

본 프로그램은 다음과 같은 파일 및 구조로 이루어져 있습니다.

### 1. 소스 코드 (`PG_monitor.py`)

- **`PostgresDashboard` 클래스**: 메인 GUI 프레임워크 및 대시보드 로직 관리
- **`SessionActionPopup` 클래스**: 세션 상세 분석 및 쿼리 실행계획 모달창
- **`LockTreePopup` 클래스**: 락 경합 상태를 계층형 트리로 시각화하는 모달창
- **`async_fetch_worker` 함수**: 비동기 데이터 수집을 담당하여 UI의 프리징 현상을 방지

### 2. 설정 파일 (`pg_config.json`)

- 프로그램 최초 연결 시 '정보 저장' 옵션을 선택하면 생성됩니다.
- DB 호스트, 포트, 계정 정보가 담겨있으며, **비밀번호는 Base64로 간이 인코딩**되어 저장됩니다.

## ⚙️ PostgreSQL 권장 설정 (로그 경로 노출)

로그 파일 경로가 `Disabled`로 나올 경우, 서버의 로깅 설정이 꺼져 있을 가능성이 높습니다. 다음 설정을 `postgresql.conf`에 적용하고 재기동하세요.

```ini
logging_collector = on       # 활성 로그 수집기 작동 (필수)
log_destination = 'stderr'
log_directory = 'log'        # 로그 파일이 저장될 디렉토리명

```

또한, 모니터링 계정에 다음 권한을 부여하면 모든 기능을 원활하게 사용할 수 있습니다.

GRANT pg_monitor TO [모니터링계정명];

## 🗂️ 프로젝트 트리 구조

```text
PG_Monitor/
├── PG_monitor.py      # 메인 실행 파일
├── pg_config.json     # (자동생성) 접속 설정 캐시
└── README.md          # 프로젝트 가이드

```
