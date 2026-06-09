# -*- coding: utf-8 -*-
import warnings
warnings.filterwarnings('ignore', category=SyntaxWarning)

import logging
import os
import re
import json
import urllib.request
import urllib.parse
import urllib.error

from ask_sdk_core.skill_builder import SkillBuilder
from ask_sdk_core.dispatch_components import AbstractRequestHandler, AbstractExceptionHandler
from ask_sdk_core.handler_input import HandlerInput
from ask_sdk_model import Response, Intent, IntentConfirmationStatus, Slot
from ask_sdk_model import ui
from ask_sdk_model.ui import Reprompt
from ask_sdk_model.dialog import ElicitSlotDirective

logger = logging.getLogger(__name__)
logger.setLevel(os.environ.get('LOG_LEVEL', 'INFO'))

FETCH_URL       = os.environ.get('FETCH_URL',       '')   # GET /rss-stories
READARTICLE_URL = os.environ.get('READARTICLE_URL', '')   # GET /read-article
MARKREAD_URL    = os.environ.get('MARKREAD_URL',    '')   # POST /mark-read
ALEXA_API_KEY   = os.environ.get('ALEXA_API_KEY',  '')
WEBCALL_TIMEOUT = int(os.environ.get('WEBCALL_TIMEOUT', '15'))

# When True Alexa adds explicit verbal control hints after each prompt.
EXPLICIT_CONTROLS = os.environ.get('EXPLICIT_CONTROLS', 'false').lower() == 'true'

# ── User map ────────────────────────────────────────────────────────────────

USER_MAP = {
    'sean':   1,
    'sharon': 2,
    'swaran': 2,
}

# ── Prompt helpers ───────────────────────────────────────────────────────────

def _story_prompt():
    if EXPLICIT_CONTROLS:
        return "Want to hear the full story? Say yes, no to skip, or stop to end."
    return "Want to hear the full story?"

def _story_reprompt():
    return "Say yes to read the article, no to skip it, or stop to end."

def _continue_prompt():
    if EXPLICIT_CONTROLS:
        return "Want me to continue? Say yes or no."
    return "Want me to continue?"

def _continue_reprompt():
    return "Say yes to continue reading, or no to move to the next story."

def _next_story_prompt():
    if EXPLICIT_CONTROLS:
        return "Ready for the next story? Say yes or no."
    return "Ready for the next story?"

def _next_story_reprompt():
    return "Say yes for the next story, or no to end."

# ── API communication ────────────────────────────────────────────────────────

def fetch_story(user_id, mode, exclude_ids=None):
    try:
        params = {'user_id': user_id, 'mode': mode}
        if exclude_ids:
            params['exclude_ids'] = ','.join(str(i) for i in exclude_ids)
        url = f"{FETCH_URL}?{urllib.parse.urlencode(params)}"
        logger.info(f"fetch_story: user_id={user_id} mode={mode} exclude_ids={exclude_ids}")
        req = urllib.request.Request(url, method='GET')
        req.add_header('X-API-Key', ALEXA_API_KEY)
        with urllib.request.urlopen(req, timeout=WEBCALL_TIMEOUT) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            if isinstance(data, list):
                data = data[0] if data else None
            logger.info(f"fetch_story: got article_id={data.get('article_id') if data else None}")
            return data
    except urllib.error.URLError as e:
        logger.error(f"fetch_story: network error {e}", exc_info=True)
        return None
    except Exception as e:
        logger.error(f"fetch_story: error {e}", exc_info=True)
        return None


def fetch_article_chunk(article_id, offset):
    try:
        params = {'article_id': article_id, 'offset': offset}
        url = f"{READARTICLE_URL}?{urllib.parse.urlencode(params)}"
        logger.info(f"fetch_article_chunk: article_id={article_id} offset={offset}")
        req = urllib.request.Request(url, method='GET')
        req.add_header('X-API-Key', ALEXA_API_KEY)
        with urllib.request.urlopen(req, timeout=WEBCALL_TIMEOUT) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            logger.info(f"fetch_article_chunk: has_more={data.get('has_more')} next_offset={data.get('next_offset')}")
            return data
    except urllib.error.URLError as e:
        logger.error(f"fetch_article_chunk: network error {e}", exc_info=True)
        return None
    except Exception as e:
        logger.error(f"fetch_article_chunk: error {e}", exc_info=True)
        return None


def mark_story_interacted(user_id, article_id, status='read'):
    try:
        payload = json.dumps({
            'user_id':    user_id,
            'article_id': article_id,
            'status':     status,
        }).encode('utf-8')
        req = urllib.request.Request(
            MARKREAD_URL,
            data=payload,
            method='POST',
            headers={
                'Content-Type': 'application/json',
                'X-API-Key':    ALEXA_API_KEY,
            }
        )
        with urllib.request.urlopen(req, timeout=WEBCALL_TIMEOUT) as resp:
            logger.info(f"mark_story_interacted: user_id={user_id} article_id={article_id} status={status} http={resp.status}")
            return True
    except urllib.error.URLError as e:
        logger.error(f"mark_story_interacted: network error {e}", exc_info=True)
        return False
    except Exception as e:
        logger.error(f"mark_story_interacted: error {e}", exc_info=True)
        return False

# ── Response helpers ─────────────────────────────────────────────────────────

def ask(handler_input, speech, reprompt=None):
    if reprompt is None:
        reprompt = speech
    return Response(
        output_speech=ui.SsmlOutputSpeech(ssml=f"<speak>{speech}</speak>"),
        reprompt=Reprompt(
            output_speech=ui.SsmlOutputSpeech(ssml=f"<speak>{reprompt}</speak>")
        ),
        directives=[ElicitSlotDirective(
            slot_to_elicit='query',
            updated_intent=Intent(
                name='FeedIntent',
                confirmation_status=IntentConfirmationStatus.NONE,
                slots={'query': Slot(name='query', value='')}
            )
        )],
        should_end_session=False
    )


def tell(handler_input, speech):
    return (
        handler_input.response_builder
        .speak(speech)
        .set_should_end_session(True)
        .response
    )

# ── Helpers ──────────────────────────────────────────────────────────────────

def log_request_debug(handler_input):
    try:
        intent = handler_input.request_envelope.request.intent
        slots  = getattr(intent, 'slots', {}) or {}
        logger.debug(f"slots: { {k: getattr(v,'value',None) for k,v in slots.items()} }")
    except Exception:
        pass


def is_stop_command(raw):
    return raw.lower() in ('stop', 'quit', 'exit', 'goodbye', 'bye', 'cancel', 'end', 'finish', 'done')


def parse_user(raw):
    return USER_MAP.get(raw.lower().strip())


def parse_mode(raw, intent_name):
    if intent_name == 'AMAZON.YesIntent' or raw.lower() in ('best', 'best stories', 'top', 'top stories', 'personalised', 'personalized'):
        return 'top'
    if raw.lower() in ('latest', 'latest news', 'news', 'recent', 'recent news', 'newest'):
        return 'latest'
    return None


def _do_mark_read(session_attr, status='read'):
    user_id    = session_attr.get('user_id')
    article_id = session_attr.get('current_article_id')
    if user_id and article_id:
        mark_story_interacted(user_id, article_id, status=status)

# ── Handlers ─────────────────────────────────────────────────────────────────

class LaunchRequestHandler(AbstractRequestHandler):

    def can_handle(self, handler_input):
        return handler_input.request_envelope.request.object_type == "LaunchRequest"

    def handle(self, handler_input):
        session_attr = handler_input.attributes_manager.session_attributes
        session_attr['state']    = 'awaiting_user'
        session_attr['seen_ids'] = []
        return ask(handler_input,
            "Who's listening?",
            reprompt="Say Sean or Swaran to get started.")


class FeedIntentHandler(AbstractRequestHandler):

    def can_handle(self, handler_input):
        if handler_input.request_envelope.request.object_type != "IntentRequest":
            return False
        name = handler_input.request_envelope.request.intent.name
        return name in ("FeedIntent", "AMAZON.YesIntent", "AMAZON.NoIntent")

    def handle(self, handler_input):
        log_request_debug(handler_input)
        session_attr = handler_input.attributes_manager.session_attributes
        state        = session_attr.get('state', 'awaiting_user')
        intent_name  = handler_input.request_envelope.request.intent.name

        slots = getattr(handler_input.request_envelope.request.intent, 'slots', {}) or {}
        slot  = slots.get('query')
        raw   = slot.value if slot and slot.value else ''

        logger.info(f"FeedIntentHandler: state={state} intent={intent_name} raw='{raw}'")

        # ── Global stop ──────────────────────────────────────────────────────
        if is_stop_command(raw):
            if state in ('reading_article',):
                _do_mark_read(session_attr, status='read')
            return tell(handler_input, "See you later!")

        # ── Step 1: identify user ────────────────────────────────────────────
        if state == 'awaiting_user':
            user_id = parse_user(raw)
            if not user_id:
                return ask(handler_input,
                    "Sorry, I didn't catch that. Say Sean or Swaran.",
                    reprompt="Say Sean or Swaran to get started.")
            session_attr['user_id'] = user_id
            session_attr['state']   = 'awaiting_mode'
            return ask(handler_input,
                "Would you like your best stories or the latest news?",
                reprompt="Say best stories for your personalised feed, or latest news for the most recent.")

        # ── Step 2: pick mode ────────────────────────────────────────────────
        if state == 'awaiting_mode':
            mode = parse_mode(raw, intent_name)
            if not mode:
                return ask(handler_input,
                    "Say best stories for your personalised feed, or latest news for the most recent.",
                    reprompt="Say best stories or latest news.")
            session_attr['fetch_mode'] = mode
            session_attr['state']      = 'reading'
            return self._fetch_and_play(handler_input, session_attr)

        # ── Step 3: reading — title presented, waiting for yes/no ────────────
        if state == 'reading':
            if raw.lower() in ('skip', 'next', 'pass', 'nah'):
                _do_mark_read(session_attr, status='skipped')
                return self._fetch_and_play(handler_input, session_attr)

            if intent_name == "AMAZON.NoIntent" or raw.lower() in ('no', 'nope', 'not interested', 'decline'):
                _do_mark_read(session_attr, status='declined')
                return self._fetch_and_play(handler_input, session_attr)

            if intent_name == "AMAZON.YesIntent" or raw.lower() in ('yes', 'yeah', 'yep', 'sure', 'go ahead', 'ok', 'okay', 'read it', 'read'):
                return self._start_reading_article(handler_input, session_attr)

            return ask(handler_input, _story_prompt(), reprompt=_story_reprompt())

        # ── Step 4: reading_article — mid-article pagination ─────────────────
        if state == 'reading_article':
            if raw.lower() in ('skip', 'next', 'stop reading', 'enough'):
                _do_mark_read(session_attr, status='read')
                session_attr['state'] = 'reading'
                return self._fetch_and_play(handler_input, session_attr)

            if intent_name == "AMAZON.NoIntent" or raw.lower() in ('no', 'nope', 'stop', 'that\'s enough', 'next story'):
                _do_mark_read(session_attr, status='read')
                session_attr['state'] = 'reading'
                return self._fetch_and_play(handler_input, session_attr)

            if intent_name == "AMAZON.YesIntent" or raw.lower() in ('yes', 'yeah', 'yep', 'sure', 'continue', 'keep going', 'go ahead'):
                return self._continue_reading(handler_input, session_attr)

            return ask(handler_input, _continue_prompt(), reprompt=_continue_reprompt())

        # ── Step 5: after_article — full article done, next story? ───────────
        if state == 'after_article':
            if intent_name == "AMAZON.YesIntent" or raw.lower() in ('yes', 'yeah', 'yep', 'sure', 'next', 'go ahead'):
                _do_mark_read(session_attr, status='read')
                session_attr['state'] = 'reading'
                return self._fetch_and_play(handler_input, session_attr)

            if intent_name == "AMAZON.NoIntent" or raw.lower() in ('no', 'nope', 'done', 'stop', 'finished'):
                _do_mark_read(session_attr, status='read')
                return tell(handler_input, "Enjoy your day!")

            return ask(handler_input, _next_story_prompt(), reprompt=_next_story_reprompt())

        # ── Fallback ─────────────────────────────────────────────────────────
        logger.warning(f"FeedIntentHandler: unrecognised state='{state}', resetting")
        session_attr['state'] = 'awaiting_user'
        return ask(handler_input,
            "Something went wrong. Who's listening — Sean or Swaran?",
            reprompt="Say Sean or Swaran to get started.")

    def _fetch_and_play(self, handler_input, session_attr):
        user_id  = session_attr.get('user_id')
        mode     = session_attr.get('fetch_mode', 'top')
        seen_ids = session_attr.get('seen_ids', [])

        story = fetch_story(user_id, mode, exclude_ids=seen_ids)

        if not story:
            return tell(handler_input, "That's all your stories for now. Check back later!")

        article_id = story.get('article_id')
        if article_id in seen_ids:
            return tell(handler_input, "That's all your fresh stories for now. Check back later!")

        title = story.get('title', 'Untitled')

        session_attr['current_article_id'] = article_id
        session_attr['state']              = 'reading'

        seen = session_attr.setdefault('seen_ids', [])
        if article_id not in seen:
            seen.append(article_id)

        logger.info(f"_fetch_and_play: article_id={article_id} title='{title[:60]}'")
        speech = f"{title}. <break time='0.5s'/> {_story_prompt()}"
        return ask(handler_input, speech, reprompt=_story_reprompt())

    def _start_reading_article(self, handler_input, session_attr):
        article_id = session_attr.get('current_article_id')
        data = fetch_article_chunk(article_id, offset=0)

        if not data or not data.get('content'):
            return ask(handler_input,
                "Sorry, I couldn't fetch the article content. Want to try the next story?",
                reprompt=_next_story_prompt())

        session_attr['current_article_offset'] = data['next_offset']
        has_more = data.get('has_more', False)

        if has_more:
            session_attr['state'] = 'reading_article'
            speech = (
                f"{data['content']}"
                f"<break time='1s'/>"
                f"{_continue_prompt()}"
            )
            return ask(handler_input, speech, reprompt=_continue_reprompt())
        else:
            session_attr['state'] = 'after_article'
            speech = (
                f"{data['content']}"
                f"<break time='1.5s'/>"
                f"That's the full story. {_next_story_prompt()}"
            )
            return ask(handler_input, speech, reprompt=_next_story_reprompt())

    def _continue_reading(self, handler_input, session_attr):
        article_id = session_attr.get('current_article_id')
        offset     = session_attr.get('current_article_offset', 0)

        data = fetch_article_chunk(article_id, offset=offset)

        if not data or not data.get('content'):
            session_attr['state'] = 'after_article'
            return ask(handler_input,
                f"That's all I have. {_next_story_prompt()}",
                reprompt=_next_story_reprompt())

        session_attr['current_article_offset'] = data['next_offset']
        has_more = data.get('has_more', False)

        if has_more:
            session_attr['state'] = 'reading_article'
            speech = (
                f"{data['content']}"
                f"<break time='1s'/>"
                f"{_continue_prompt()}"
            )
            return ask(handler_input, speech, reprompt=_continue_reprompt())
        else:
            session_attr['state'] = 'after_article'
            speech = (
                f"{data['content']}"
                f"<break time='1.5s'/>"
                f"That's the end of the article. {_next_story_prompt()}"
            )
            return ask(handler_input, speech, reprompt=_next_story_reprompt())


class HelpIntentHandler(AbstractRequestHandler):

    def can_handle(self, handler_input):
        return (
            handler_input.request_envelope.request.object_type == "IntentRequest" and
            handler_input.request_envelope.request.intent.name == "AMAZON.HelpIntent"
        )

    def handle(self, handler_input):
        return ask(handler_input,
            "Say Sean or Swaran to start. I'll read you news headlines. "
            "Say yes to hear the full article, no to skip to the next story, "
            "or stop to end.",
            reprompt="Say Sean or Swaran to get started.")


class StopIntentHandler(AbstractRequestHandler):

    def can_handle(self, handler_input):
        return (
            handler_input.request_envelope.request.object_type == "IntentRequest" and
            handler_input.request_envelope.request.intent.name in (
                "AMAZON.StopIntent", "AMAZON.CancelIntent"
            )
        )

    def handle(self, handler_input):
        session_attr = handler_input.attributes_manager.session_attributes
        state = session_attr.get('state')
        if state == 'reading_article':
            _do_mark_read(session_attr, status='read')
        return tell(handler_input, "See you later!")


class FallbackIntentHandler(AbstractRequestHandler):

    def can_handle(self, handler_input):
        return (
            handler_input.request_envelope.request.object_type == "IntentRequest" and
            handler_input.request_envelope.request.intent.name == "AMAZON.FallbackIntent"
        )

    def handle(self, handler_input):
        session_attr = handler_input.attributes_manager.session_attributes
        state = session_attr.get('state', 'awaiting_user')
        if state == 'awaiting_user':
            return ask(handler_input, "Say Sean or Swaran to get started.", reprompt="Say Sean or Swaran.")
        if state == 'awaiting_mode':
            return ask(handler_input, "Say best stories or latest news.", reprompt="Say best stories or latest news.")
        if state == 'reading':
            return ask(handler_input, _story_prompt(), reprompt=_story_reprompt())
        if state == 'reading_article':
            return ask(handler_input, _continue_prompt(), reprompt=_continue_reprompt())
        if state == 'after_article':
            return ask(handler_input, _next_story_prompt(), reprompt=_next_story_reprompt())
        return ask(handler_input, "Say Sean or Swaran to get started.", reprompt="Say Sean or Swaran.")


class SessionEndedRequestHandler(AbstractRequestHandler):

    def can_handle(self, handler_input):
        return handler_input.request_envelope.request.object_type == "SessionEndedRequest"

    def handle(self, handler_input):
        session_attr = handler_input.attributes_manager.session_attributes
        state = session_attr.get('state')
        if state == 'reading_article':
            _do_mark_read(session_attr, status='read')
        return handler_input.response_builder.response


class CatchAllExceptionHandler(AbstractExceptionHandler):

    def can_handle(self, handler_input, exception):
        return True

    def handle(self, handler_input, exception):
        logger.error(f"CatchAllExceptionHandler: {exception}", exc_info=True)
        return tell(handler_input, "Sorry, something went wrong. Please try again.")


# ── Skill builder ─────────────────────────────────────────────────────────────

sb = SkillBuilder()
sb.add_request_handler(LaunchRequestHandler())
sb.add_request_handler(FeedIntentHandler())
sb.add_request_handler(HelpIntentHandler())
sb.add_request_handler(StopIntentHandler())
sb.add_request_handler(FallbackIntentHandler())
sb.add_request_handler(SessionEndedRequestHandler())
sb.add_exception_handler(CatchAllExceptionHandler())

lambda_handler = sb.lambda_handler()
