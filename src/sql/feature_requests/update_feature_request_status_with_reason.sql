UPDATE feature_requests
SET status = :status,
    closure_reason = CASE
        WHEN :status IN ('completed','not_doing','merged') THEN :closure_reason
        ELSE closure_reason
    END,
    closed_by = CASE
        WHEN :status IN ('completed','not_doing','merged') THEN :closed_by
        ELSE NULL
    END,
    closed_at = CASE
        WHEN :status IN ('completed','not_doing','merged') THEN NOW()
        ELSE NULL
    END,
    last_modified = NOW()
WHERE id = :request_id;
