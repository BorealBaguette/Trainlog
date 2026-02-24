CREATE TABLE operators (
    operator_id SERIAL PRIMARY KEY,
    operator_type VARCHAR(100) NOT NULL,
    short_name VARCHAR(100),
    long_name VARCHAR(200)
);

CREATE TABLE operator_logos (
    uid SERIAL PRIMARY KEY,
    operator_id INTEGER NOT NULL references operators(operator_id) ON DELETE CASCADE,
    logo_url TEXT,
    effective_date DATE
);