from src.sql import SqlTemplate

# Feature Requests queries
list_feature_requests = SqlTemplate(
    "src/sql/feature_requests/list_feature_requests.sql"
)
list_feature_requests_with_votes = SqlTemplate(
    "src/sql/feature_requests/list_feature_requests_with_votes.sql"
)
list_feature_requests_by_date = SqlTemplate(
    "src/sql/feature_requests/list_feature_requests_by_date.sql"
)
list_feature_requests_with_votes_by_date = SqlTemplate(
    "src/sql/feature_requests/list_feature_requests_with_votes_by_date.sql"
)
get_single_feature_request = SqlTemplate(
    "src/sql/feature_requests/get_single_feature_request.sql"
)
get_single_feature_request_with_vote = SqlTemplate(
    "src/sql/feature_requests/get_single_feature_request_with_vote.sql"
)
insert_feature_request = SqlTemplate(
    "src/sql/feature_requests/insert_feature_request.sql"
)
update_feature_request_status = SqlTemplate(
    "src/sql/feature_requests/update_feature_request_status.sql"
)
get_feature_request_author = SqlTemplate(
    "src/sql/feature_requests/get_feature_request_author.sql"
)
update_feature_request = SqlTemplate(
    "src/sql/feature_requests/update_feature_request.sql"
)
delete_feature_request = SqlTemplate(
    "src/sql/feature_requests/delete_feature_request.sql"
)
delete_all_votes_for_request = SqlTemplate(
    "src/sql/feature_requests/delete_all_votes_for_request.sql"
)
get_feature_request_details = SqlTemplate(
    "src/sql/feature_requests/get_feature_request_details.sql"
)

# Voting queries
insert_vote = SqlTemplate("src/sql/feature_requests/insert_vote.sql")
update_vote = SqlTemplate("src/sql/feature_requests/update_vote.sql")
delete_vote = SqlTemplate("src/sql/feature_requests/delete_vote.sql")
get_user_vote = SqlTemplate("src/sql/feature_requests/get_user_vote.sql")
update_vote_counts = SqlTemplate("src/sql/feature_requests/update_vote_counts.sql")

# Voters list
list_voters = SqlTemplate("src/sql/feature_requests/list_voters.sql")

merge_votes_into_target = SqlTemplate(
    "src/sql/feature_requests/merge_votes_into_target.sql"
)
update_feature_request_status_with_reason = SqlTemplate(
    "src/sql/feature_requests/update_feature_request_status_with_reason.sql"
)

# Comments queries
list_comments = SqlTemplate("src/sql/feature_requests/list_comments.sql")
insert_comment = SqlTemplate("src/sql/feature_requests/insert_comment.sql")
update_comment = SqlTemplate("src/sql/feature_requests/update_comment.sql")
delete_comment = SqlTemplate("src/sql/feature_requests/delete_comment.sql")
get_comment_author = SqlTemplate("src/sql/feature_requests/get_comment_author.sql")
