SELECT
    id,
    title,
    description,
    username,
    status,
    created,
    upvotes,
    downvotes,
    score,
    0 AS user_vote,
    closure_reason,
    closed_by,
    closed_at
FROM feature_requests
ORDER BY score DESC, created DESC
