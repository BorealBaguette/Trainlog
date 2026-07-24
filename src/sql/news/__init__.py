from src.sql import SqlTemplate

# Import all SQL templates for news module
list_news = SqlTemplate("src/sql/news/list_news.sql")
get_single_news = SqlTemplate("src/sql/news/get_single_news.sql") 
insert_news = SqlTemplate("src/sql/news/insert_news.sql")
update_news = SqlTemplate("src/sql/news/update_news.sql")
delete_news = SqlTemplate("src/sql/news/delete_news.sql")
get_news_author = SqlTemplate("src/sql/news/get_news_author.sql")
count_news_since_date = SqlTemplate("src/sql/news/count_news_since_date.sql")
count_unseen_news = SqlTemplate("src/sql/news/count_unseen_news.sql")
upsert_news_visit = SqlTemplate("src/sql/news/upsert_news_visit.sql")
add_reaction = SqlTemplate("src/sql/news/add_reaction.sql")
remove_reaction = SqlTemplate("src/sql/news/remove_reaction.sql")
list_reactions = SqlTemplate("src/sql/news/list_reactions.sql")
list_all_reactions = SqlTemplate("src/sql/news/list_all_reactions.sql")
