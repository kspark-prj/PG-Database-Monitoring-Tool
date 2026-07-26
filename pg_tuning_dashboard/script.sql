-- 1. 기존 테스트 테이블이 있다면 삭제 (초기화)
DROP TABLE IF EXISTS orders_dummy CASCADE;
DROP TABLE IF EXISTS customers_dummy CASCADE;

-- 2. 고객 테이블 생성
CREATE TABLE customers_dummy (
    customer_id SERIAL PRIMARY KEY,
    name VARCHAR(50),
    email VARCHAR(100),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 3. 주문 테이블 생성 (성능 병목 유도를 위해 인덱스를 일부러 누락시키거나 비효율적으로 설계)
CREATE TABLE orders_dummy (
    order_id SERIAL PRIMARY KEY,
    customer_id INT,
    order_date TIMESTAMP,
    amount NUMERIC(10, 2),
    status VARCHAR(20),
    comment TEXT
);

-- 4. 튜닝 대상(미사용 인덱스) 확인을 위한 무거운 인덱스 강제 생성
-- (실제 조회에는 쓰이지 않는 인덱스를 만들어 '인덱스 미사용 테이블' 메뉴에 걸리게 합니다)
CREATE INDEX idx_orders_comment_dummy ON orders_dummy(comment);
CREATE INDEX idx_orders_status_date_dummy ON orders_dummy(status, order_date);


-- 1. 고객 더미 데이터 1,000건 삽입
INSERT INTO customers_dummy (name, email)
SELECT
    'User_' || i,
    'user_' || i || '@example.com'
FROM generate_series(1, 1000) AS i;

-- 2. 주문 더미 데이터 500,000건 대량 삽입 (Full Scan 및 쿼리 지연 유도용)
INSERT INTO orders_dummy (customer_id, order_date, amount, status, comment)
SELECT
    floor(random() * 1000 + 1)::int,
    clock_timestamp() - (random() * interval '365 days'),
    (random() * 500 + 10)::numeric(10,2),
    (ARRAY['PENDING', 'COMPLETED', 'CANCELLED', 'SHIPPED'])[floor(random() * 4 + 1)],
    md5(random()::text) -- 임의의 긴 문자열 생성
FROM generate_series(1, 500000) AS i;


-- 인덱스가 없어 50만건을 풀스캔해야 하는 무거운 쿼리
SELECT status, SUM(amount), COUNT(*)
FROM orders_dummy
WHERE amount > 450.00 AND comment LIKE '%a%'
GROUP BY status;



-- 5만 건의 데이터를 순간적으로 업데이트 (大量 Dead Tuple 발생)
UPDATE orders_dummy
SET status = 'CANCELLED'
WHERE customer_id BETWEEN 100 AND 200;

-- 2만 건의 데이터 삭제
DELETE FROM orders_dummy
WHERE order_id % 25 = 0;

