-- ====================================================================
-- [준비 작업] 더미 테이블 및 대용량 데이터 생성
-- ====================================================================
DROP TABLE IF EXISTS test_heavy_table;

CREATE TABLE test_heavy_table (
    id SERIAL PRIMARY KEY,
    name TEXT,
    created_at TIMESTAMP DEFAULT now(),
    description TEXT
);


INSERT INTO test_heavy_table (name, description)
SELECT
    'user_' || g,
    repeat('PostgreSQL DBA SOP Test Session Monitoring Case! ', 5)
FROM generate_series(1, 500000) g;

-- ====================================================================
-- 시나리오 1: 백그라운드 작업 (Progress Monitor 테스트)
-- ====================================================================
-- 진행률 대시보드(pg_stat_progress_create_index) 수초 간 포착용 설정
SET max_parallel_maintenance_workers = 0;
SET maintenance_work_mem = '10MB';

CREATE INDEX idx_test_heavy_desc ON test_heavy_table(description);


-- ====================================================================
-- 시나리오 2: 3단계 다단계 락 트리 (Cascading Lock Tree 테스트)
-- 세션 3개를 순차 실행하여 Session 1 -> Session 2 -> Session 3 계층 생성
-- ====================================================================

-- [쿼리 창 1: Session 1] - Root Blocker
BEGIN;
UPDATE test_heavy_table SET name = 'blocker_root' WHERE id = 100;
-- (COMMIT/ROLLBACK 하지 않고 대기)


-- [쿼리 창 2: Session 2] - Intermediate Blocker & Waiting
BEGIN;
UPDATE test_heavy_table SET name = 'blocker_mid' WHERE id = 200; -- id=200 선점
UPDATE test_heavy_table SET name = 'blocker_mid_wait' WHERE id = 100; -- id=100 대기 빠짐


-- [쿼리 창 3: Session 3] - Pure Waiting Session
BEGIN;
UPDATE test_heavy_table SET name = 'zombie_waiting' WHERE id = 200; -- id=200 대기 빠짐


-- ====================================================================
-- 시나리오 3: 테이블 전체 배타 락 (ACCESS EXCLUSIVE MODE)
-- DML 외에 SELECT조차 멈추는 대형 장애 상황 연출
-- ====================================================================

-- [쿼리 창 4: Session 4]
BEGIN;
LOCK TABLE test_heavy_table IN ACCESS EXCLUSIVE MODE;

-- [쿼리 창 5: Session 5]
SELECT count(*) FROM test_heavy_table; -- Blocked!


-- ====================================================================
-- [테스트 종료 후 Clean Up]
-- ====================================================================
-- DROP TABLE IF EXISTS test_heavy_table;
