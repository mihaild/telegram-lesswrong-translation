import dataclasses
import feedparser


@dataclasses.dataclass
class LesswrongPost:
    url: str
    title: str
    html: str


def get_post_url(post: dict) -> str:
    return f"https://www.lesswrong.com/posts/{post['_id']}/{post['slug']}"


def get_last_posts() -> LesswrongPost:
    feed = feedparser.parse('https://www.lesswrong.com/feed.xml?view=frontpage-rss&karmaThreshold=15')
    if feed.status != 200:
        raise ValueError(f'Feed error {feed!r}')
    return [LesswrongPost(
        url=entry.link,
        title=entry.title,
        html=entry.summary,
    ) for entry in feed.entries]

