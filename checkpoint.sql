-- 1. 더미 테이블 생성
CREATE TABLE test_heavy_table (
    id SERIAL PRIMARY KEY,
    name TEXT,
    created_at TIMESTAMP DEFAULT now(),
    description TEXT
);

-- 2. 약 50만 건의 대용량 더미 데이터 삽입 (수 초 소요)
INSERT INTO test_heavy_table (name, description)
SELECT
    'user_' || g,
    repeat('PostgreSQL DBA SOP Test Session Monitoring Case! ', 5)
FROM generate_series(1, 500000) g;

--시나리오 A: 대용량 테이블 인덱스 강제 생성테이블이 수십만 건 이상이므로 인덱스를 생성할 때 약 수 초~수십 초 동안 단계별 진행 상황(scanning table $\rightarrow$ sorting $\rightarrow$ building index)이 대시보드에 실시간으로 기록됩니다.SQL-- 일부러 병렬 처리 및 메모리를 제한하여 천천히 돌게 유도 (테스트 모니터링 시간 확보용)
SET max_parallel_maintenance_workers = 0;
SET maintenance_work_mem = '10MB';

-- 인덱스 생성 실행 (대시보드 중앙의 Progress Monitor를 관찰하세요)
CREATE INDEX idx_test_heavy_desc ON test_heavy_table(description);
--시나리오 B: 강제 Vacuum (정리 작업) 발생PostgreSQL의 가비지 컬렉터인 Vacuum을 강제로 돌려 pg_stat_progress_vacuum에 데이터가 잡히는지 테스트합니다.SQL-- 1. 대용량 데이터 삭제 (가비지 데이터 대량 생성)
DELETE FROM test_heavy_table WHERE id % 2 = 0;

-- 2. 강제 풀 스캔 Vacuum 실행 (대시보드 Progress Monitor 관찰)
VACUUM (VERBOSE, ANALYZE) test_heavy_table;

--2. [세션 브라우저 및 락 트리] 영역 테스트하단의 Live Session Browser와 우측 상단의 Lock Tree 버튼을 테스트하기 위해 의도적으로 배타적 락(Exclusive Lock) 경합을 유발합니다.이 테스트를 위해서는 최소 2개의 별도 쿼리 창(Session 1, Session 2)이 필요합니다.1단계: 세션 1 (Lock을 선점하여 다른 세션을 대기시키는 트랜잭션)첫 번째 쿼리 창에서 아래 쿼리를 차례대로 실행하고 커밋하지 않은 상태(BEGIN 유지)로 둡니다.SQLBEGIN;
-- 특정 데이터의 행을 독점 잠금 처리
SELECT * FROM test_heavy_table
WHERE id = 100
FOR UPDATE;

-- (여기서 COMMIT이나 ROLLBACK을 하지 않고 그대로 유지합니다)
2단계: 세션 2 (Lock에 막혀 무한 대기하는 슬로우 세션)두 번째 쿼리 창에서 동일한 행을 수정하려고 시도합니다. 이 세션은 1단계 세션이 끝날 때까지 락에 막혀 무한히 대기(Blocked)하게 됩니다.SQL-- 실행하면 쿼리가 완료되지 않고 멈춰 서 대기하게 됩니다.
UPDATE test_heavy_table
SET name = 'locked_user'
WHERE id = 100;
3단계: 대시보드에서 결과 확인 및 비상 조치(SOP)이 상태에서 대시보드로 돌아가면 다음과 같은 실시간 변화를 관찰할 수 있습니다.차트 변화: ASH 차트에서 빨간색(Lock) 영역 그래프가 솟구치기 시작합니다.세션 브라우저: 상태가 active이면서 wait_event 컬럼이 Lock:transactionid 또는 Lock:tuple로 표기된 무한 대기 세션이 잡힙니다.Lock Tree 팝업: 대시보드 하단의 🔒 Lock Tree 버튼을 누르면, 독점 락을 잡고 있어 서비스 마비를 일으킨 범인 세션(Blocker)과 이로 인해 대기하고 있는 좀비 세션(Waiting)이 트리 구조(계층형)로 시각화됩니다.강제 종료 테스트: Lock Tree 창에서 원인 세션(Blocker)을 우클릭하여 Kill Session을 날려봅니다. 세션이 강제 종료되면서 2단계에서 대기 중이던 세션이 즉시 정상 처리되고 락 경합이 깔끔하게 해결되는 과정을 눈으로 확인할 수 있습니다.3. 테스트 종료 후 더미 데이터 삭제 (Clean Up)테스트가 끝난 후 운영 공간 낭비를 방지하기 위해 생성했던 더미 테이블을 완전히 삭제해 줍니다.SQLDROP TABLE IF EXISTS test_heavy_table;
