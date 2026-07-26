-- =============================================================================
-- PostgreSQL 성능 모니터링, 인덱스 진단 및 정밀 조절 SQL 종합 가이드
-- =============================================================================
-- [인덱스 크기 판단 기준 (Index Size Benchmark)]
-- 1. 단일 인덱스 1개: 테이블 데이터 크기의 10% ~ 30% 수준이 정상
-- 2. 전체 인덱스 합계: 테이블 데이터 크기의 30% ~ 50% 수준이 이상적
-- 3. 상태 진단 수치:
--    - 50% 미만 : 🟢 양호 (인덱스 관리 상태 매우 좋음)
--    - 50% ~ 100%: 🟡 주의 (복합 인덱스 수 또는 문자열 컬럼 인덱스 점검 필요)
--    - 100% 이상 : 🔴 위험/Bloat (인덱스가 테이블보다 큼. 단편화 제거 또는 미사용 인덱스 정리 필요)
-- =============================================================================

--------------------------------------------------------------------------------
-- 1. [시스템 현황] 현재 DB 커넥션 및 한도 확인
--------------------------------------------------------------------------------
-- 설정된 최대 커넥션 수 확인
SHOW max_connections;

-- 현재 접속된 활성 커넥션 수 확인
SELECT count(*) FROM pg_stat_activity;


--------------------------------------------------------------------------------
-- 2. [디스크 사용량] 테이블 및 인덱스별 용량 조회 (Top 50)
--------------------------------------------------------------------------------
-- 데이터 크기, 인덱스 크기, 총 용량 및 전체 라이브 튜플 수 확인
SELECT 
    schemaname AS schema, 
    relname AS table_name,
    pg_size_pretty(pg_relation_size(relid)) AS data_size,
    pg_size_pretty(pg_total_relation_size(relid) - pg_relation_size(relid)) AS index_size,
    pg_size_pretty(pg_total_relation_size(relid)) AS total_size,
    n_live_tup AS row_count
FROM pg_stat_user_tables 
ORDER BY pg_total_relation_size(relid) DESC 
LIMIT 50;


--------------------------------------------------------------------------------
-- 3. [슬로우 쿼리] 총 실행시간 상위 10개 쿼리 (pg_stat_statements 필요)
--------------------------------------------------------------------------------
-- 시스템 누적 소모 시간(total_exec_time) 및 건당 평균 실행 시간(avg_time_ms) 추출
SELECT 
    round(total_exec_time::numeric, 2) AS total_time_ms, 
    calls,
    round((total_exec_time / calls)::numeric, 2) AS avg_time_ms,
    round(rows::numeric, 0) AS total_rows, 
    query
FROM pg_stat_statements 
WHERE query NOT LIKE '%pg_stat_statements%'
ORDER BY total_exec_time DESC 
LIMIT 10;


--------------------------------------------------------------------------------
-- 4. [Full Scan] 순차 스캔(Sequential Scan)이 빈번한 테이블 (Top 20)
--------------------------------------------------------------------------------
-- 풀스캔 횟수, 읽은 행 수 및 1회 스캔당 평균 읽은 행 수를 계산하여 인덱스 필요성 진단
SELECT 
    schemaname, 
    relname AS table_name, 
    seq_scan, 
    seq_tup_read, 
    idx_scan,
    CASE 
        WHEN seq_scan > 0 THEN round((seq_tup_read / seq_scan)::numeric, 2) 
        ELSE 0 
    END AS avg_rows_per_scan
FROM pg_stat_user_tables 
ORDER BY seq_scan DESC 
LIMIT 20;


--------------------------------------------------------------------------------
-- 5. [미사용 인덱스] 조회 스캔(idx_scan)이 0인 인덱스 목록
--------------------------------------------------------------------------------
-- 생성 후 한 번도 조회에 활용되지 않아 CUD 성능만 저하시키는 인덱스 추출
SELECT 
    schemaname, 
    relname AS table_name, 
    indexrelname AS index_name, 
    idx_scan,
    pg_size_pretty(pg_relation_size(indexrelid)) AS index_size
FROM pg_stat_user_indexes 
WHERE idx_scan = 0 
  AND idx_scan IS NOT NULL
ORDER BY pg_relation_size(indexrelid) DESC;


--------------------------------------------------------------------------------
-- 6. [Dead Tuples] 진공(Vacuum) 대상 및 dead tuple 비율 확인
--------------------------------------------------------------------------------
-- 테이블 내 데드 튜플 비율(dead_tuple_ratio_pct)이 높을 경우 성능 저하 유발
SELECT 
    schemaname, 
    relname AS table_name, 
    n_dead_tup, 
    n_live_tup,
    round((n_dead_tup::numeric / (n_live_tup + n_dead_tup + 1)::numeric) * 100, 2) AS dead_tuple_ratio_pct,
    last_vacuum, 
    last_autovacuum
FROM pg_stat_user_tables 
ORDER BY n_dead_tup DESC;


--------------------------------------------------------------------------------
-- 7. [인덱스 캐시 히트율] 메모리(Shared Buffers) 히트 비율 추적 (Top 50)
--------------------------------------------------------------------------------
-- 디스크 I/O 발생 블록 수 대비 캐시 히트 블록 수를 계산 (99% 이상 권장)
SELECT 
    schemaname,
    relname AS table_name,
    indexrelname AS index_name,
    idx_blks_read AS disk_read_blocks,
    idx_blks_hit AS cache_hit_blocks,
    ROUND((idx_blks_hit::numeric / NULLIF(idx_blks_hit + idx_blks_read, 0)) * 100, 2) AS cache_hit_pct
FROM pg_statio_user_indexes
WHERE (idx_blks_hit + idx_blks_read) > 0
ORDER BY idx_blks_read DESC 
LIMIT 50;


--------------------------------------------------------------------------------
-- 8. [인덱스 Bloat 진단] 인덱스 대 테이블 크기 비율 분석 (Top 50)
--------------------------------------------------------------------------------
-- index_ratio_pct가 100% 이상이면 단편화(Bloat) 또는 과도한 인덱스로 진단
SELECT 
    i.schemaname,
    i.relname AS table_name,
    i.indexrelname AS index_name,
    pg_size_pretty(pg_relation_size(i.indexrelid)) AS index_size,
    pg_size_pretty(pg_relation_size(i.relid)) AS table_size,
    ROUND((pg_relation_size(i.indexrelid)::numeric / NULLIF(pg_relation_size(i.relid), 0)) * 100, 2) AS index_ratio_pct,
    CASE 
        WHEN ROUND((pg_relation_size(i.indexrelid)::numeric / NULLIF(pg_relation_size(i.relid), 0)) * 100, 2) >= 100 THEN '🔴 위험 (Bloat 의심)'
        WHEN ROUND((pg_relation_size(i.indexrelid)::numeric / NULLIF(pg_relation_size(i.relid), 0)) * 100, 2) >= 50 THEN '🟡 주의 (점검 필요)'
        ELSE '🟢 양호'
    END AS status_check
FROM pg_stat_user_indexes i
ORDER BY pg_relation_size(i.indexrelid) DESC 
LIMIT 50;


--------------------------------------------------------------------------------
-- 9. [작업 모니터링] 진행 중인 인덱스 생성 및 REINDEX 실시간 현황
--------------------------------------------------------------------------------
-- CREATE INDEX CONCURRENTLY 또는 REINDEX 진행 단계(phase) 및 처리율(%) 모니터링
SELECT 
    pid,
    phase,
    blocks_total,
    blocks_done,
    ROUND((blocks_done::numeric / NULLIF(blocks_total, 0)) * 100, 2) AS progress_pct,
    tuples_total,
    tuples_done
FROM pg_stat_progress_create_index;


--------------------------------------------------------------------------------
-- 10. [인덱스 정밀 제어 및 단편화 해결 DDL 샘플]
--------------------------------------------------------------------------------
-- ① 서비스 락 최소화 인덱스 생성 (CONCURRENTLY 필수)
-- CREATE INDEX CONCURRENTLY idx_users_created_at ON public.users (created_at);

-- ② 서비스 락 최소화 인덱스 삭제 (CONCURRENTLY 필수)
-- DROP INDEX CONCURRENTLY public.idx_users_created_at;

-- ③ 인덱스 단편화(Bloat) 해결을 위한 온라인 재생성 (REINDEX)
-- REINDEX INDEX CONCURRENTLY public.idx_users_created_at;