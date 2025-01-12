import json
import requests
import dataclasses

LESSWRONG_JSON_PATH = 'lesswrong.json'


@dataclasses.dataclass
class LesswrongPost:
    url: str
    title: str
    html: str


def get_post_url(post: dict) -> str:
    return f"https://www.lesswrong.com/posts/{post['_id']}/{post['slug']}"


def get_last_posts(data_file: str = LESSWRONG_JSON_PATH) -> LesswrongPost:
    config = json.load(open(data_file))
    response = requests.post('https://www.lesswrong.com/graphql', cookies={}, headers=config['headers'], json=config['last_posts_request'])
    j = json.loads(response.content.decode().strip())
    posts = j[0]['data']['posts']['results']
    return [LesswrongPost(
        url=get_post_url(post),
        title=post['title'],
        html=post['contents']['html'],
    ) for post in posts if post['contents']] # TODO: what are posts without content?

