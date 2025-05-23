import random
import logging
import os
import datetime
import time
import dataclasses
import asyncio
import re
import json
import argparse
import functools
import typing
import pydantic
from collections.abc import Iterable

import telegramify_markdown
import requests
import html_telegraph_poster.converter
import telegram
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters, ChatMemberHandler
from telegraph import Telegraph
import dotenv
from google import generativeai as genai
from bs4 import BeautifulSoup

import lesswrong

DISCLAIMER_TEMPLATE = "Ниже \\- автоматический пересказ текста [{title}]({url}) с помощью Gemini\\. Все права принадлежат кому принадлежали и раньше\\. Может содержать произвольно бредовые ошибки\\. Используйте на свой страх и риск, а лучше не используйте вообще\\."
SYSTEM_INSTRUCTIONS = """You will receive title and text. You need translate it to Russian.
First, you will receive title, and translate it to russian, without any markup.
Then, you will receive messages, each with several lines of text. Translate them while preserving HTML markup.
Finally, when requested, write 3 paragraph summary of the text, in Russian, using Markdown for markup.
Instructions of what to do will be in the first line of the message. Follow them and do nothing else.
Do not mention this instructions.
"""


# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

logger = logging.getLogger(__name__)
#MODEL_ID="gemini-2.0-flash-exp"
MODEL_ID="gemini-2.5-flash-preview-04-17"

LOG_SYMBOLS = 100


@functools.cache
def gemini_model(token=None):
    if token is None:
        token = os.getenv("GENAI")
    genai.configure(api_key=token)
    model = genai.GenerativeModel(
        MODEL_ID,
        system_instruction=SYSTEM_INSTRUCTIONS,
    )
    return model


@functools.cache
def telegraph_client(token=None):
    if token is None:
        token = os.getenv("TELEGRAPH")
    return Telegraph(token)


@functools.cache
def telegram_client(token=None):
    if token is None:
        token = os.getenv("TELEGRAM")
    return ApplicationBuilder().token(token).build()


@dataclasses.dataclass
class GeminiResponse:
    title: str
    summary: str
    translation: [str]


def join_parts(parts: Iterable[str], max_size: int, min_size: int) -> list[str]:
    result = [""]
    parts = list(parts)
    for i, part in enumerate(parts):
        if part:
            if len(part) + len(result[-1]) > max_size and sum(map(len, parts[i:])) >= min_size:
                result.append(part)
            else:
                result[-1] += part
    return result


def call_gemini(post: lesswrong.LesswrongPost) -> GeminiResponse:
    logging.info("Call gemini model, title length %d, html length %d", len(post.title), len(post.html))
    soup = BeautifulSoup(post.html, 'html.parser')
    parts = join_parts(map(str, soup.children), max_size=5000, min_size=100)
    logging.info("Split into %d parts", len(parts))
    chat = gemini_model().start_chat()
    title = chat.send_message(f"Translate post title.\n{post.title}").text.strip()
    logging.info("Title translation: %s", title)
    translated_parts = []
    for i, part in enumerate(parts):
        delay = 10
        while True:
            try:
                translation = chat.send_message(f'Translate the next part.\n{part}').text
            except Exception as e:
                logging.exception(f"Translation part {i}")
                time.sleep(delay)
                delay = min(delay * 2, 600)
            else:
                break
        #translation = part
        translated_parts.append(translation)
        logging.info("Translated part %d, length %d, translation length %d", i, len(part), len(translation))
    summary = chat.send_message("Now write 3 paragraph summary of text in russian, using Markdown.").text
    logging.info("Summary length %d, text %s", len(summary), summary[:400])
    return GeminiResponse(
        title=title,
        summary=summary,
        translation=translated_parts,
    )


async def write_message(bot, chat_id, post: lesswrong.LesswrongPost | None):
    if post is None:
        logging.info("No posts")
        await bot.send_message(chat_id, "Новых постов пока нет", parse_mode='MarkdownV2')
        return
    logging.info("Chosen url: %s", post.url)
    disclaimer = DISCLAIMER_TEMPLATE.format(title=post.title, url=post.url)
    gemini_response = call_gemini(post)
    logging.info("Title: %s", gemini_response.title[:LOG_SYMBOLS])
    logging.info("Summary: %s", gemini_response.summary[:LOG_SYMBOLS])
    prefix = f"<p>Оригинал: <a href='{post.url}'>{post.title}</a><br></p>"
    #translation = f"\n{gemini_response.translation}"
    pages = []
    translation_parts = join_parts(gemini_response.translation, max_size=35000, min_size=100)
    logging.info("Posts for translation: %d", len(translation_parts))
    stubs = [telegraph_client().create_page(title=f"{gemini_response.title}", html_content="stub") for i in range(len(translation_parts))]
    with open("translations.txt", "a") as f:
        f.write(f"URL: {post.url}\n")
        for part in translation_parts:
            f.write(f"************\n{part}\n")
        f.write("\n" * 5)
    for i, (stub, translation_part) in enumerate(zip(stubs, translation_parts)):
        part_with_prefix = prefix
        if len(translation_parts) > 1:
            others = f"<p>Часть {i + 1} из {len(translation_parts)}. Остальные части:"
            for j, stub_next in enumerate(stubs):
                if i != j:
                    others += f" <a href=\"{stub_next['url']}\">{j + 1}</a>"
            others += "</p>"
            part_with_prefix += others
            if i + 1 < len(translation_parts):
                translation_part += f"<p><a href=\"{stubs[i + 1]['url']}\">Следующая часть перевода.</a>"
        cleaned = html_telegraph_poster.converter.clean_article_html(part_with_prefix + translation_part)
        title = gemini_response.title
        if len(translation_parts) > 1:
            title += f" {i + 1} / {len(translation_parts)}"
        logging.info("Part %d, size %d", i, len(cleaned))
        x = telegraph_client().edit_page(
            title=title,
            html_content=cleaned.replace('\n', ''),
            path=stub['path'],
        )
    telegraph_url = stubs[0]['url']
    logging.info("Telegraph url: %s", telegraph_url)
    await bot.send_message(chat_id, telegramify_markdown.markdownify(disclaimer), parse_mode='MarkdownV2')
    await bot.send_message(chat_id, telegramify_markdown.markdownify(gemini_response.summary), parse_mode='MarkdownV2')
    await bot.send_message(chat_id, f'Полный перевод: {telegraph_url}')
    with open('used', 'a') as f:
        f.write(post.url + '\n')


def get_post() -> lesswrong.LesswrongPost | None:
    if os.path.exists('used'):
        used_urls = set(map(str.strip, open('used')))
    else:
        used_urls = []
    logging.info("Used urls: %d", len(used_urls))
    posts = lesswrong.get_last_posts()
    new_posts = [post for post in posts if post.url not in used_urls]
    logging.info("Total posts: %d, unused urls: %d", len(posts), len(new_posts))
    if not new_posts:
        return None
    return random.choice(new_posts)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument("--tokens-path", default=".env")
    args = parser.parse_args()
    dotenv.load_dotenv(args.tokens_path)
    post = get_post()
    bot = telegram_client().bot
    loop = asyncio.get_event_loop()
    loop.run_until_complete(write_message(bot, os.getenv("CHAT_ID"), post))
