# -*- coding: utf-8 -*-
import warnings
warnings.filterwarnings('ignore', category=SyntaxWarning)

import logging
import os
import json
import urllib.request
import urllib.parse
import urllib.error

from ask_sdk_core.skill_builder import SkillBuilder
from ask_sdk_core.dispatch_components import AbstractRequestHandler, AbstractExceptionHandler
from ask_sdk_core.handler_input import HandlerInput

logger = logging.getLogger(__name__)
logger.setLevel(os.environ.get('LOG_LEVEL', 'DEBUG'))

FETCH_URL       = os.environ.get('FETCH_URL',       '')
READARTICLE_URL = os.environ.get('READARTICLE_URL', '')
MARKREAD_URL    = os.environ.get('MARKREAD_URL',    '')
ALEXA_API_KEY   = os.environ.get('ALEXA_API_KEY',  '')
WEBCALL_TIMEOUT = int(os.environ.get('WEBCALL_TIMEOUT', '15'))
DEFAULT_USER_ID = int(os.environ.get('DEFAULT_USER_ID', '1'))
EXPLICIT_CONTROLS = os.environ.get('EXPLICIT_CONTROLS', 'false').lower() == 'true'

logger.info(f"INIT: FETCH_URL={'set' if FETCH_URL else 'MISSING'} "
            f"READARTICLE_URL={'set' if READARTICLE_URL else 'MISSING'} "
            f"MARKREAD_URL={'set' if MARKREAD_URL else 'MISSING'} "
            f"ALEXA_API_KEY={'set' if ALEXA_API_KEY else 'MISSING'} "
            f"EXPLICIT_CONTROLS={EXPLICIT_CONTROLS}")

# ── Debug logging helpers ─────────────────────────────────────────────────────

def log_request_debug(handler_input):
    request = handler_input.request_envelope.request
    session_attr = handler_input.attributes_manager.session_attributes
    logger.debug(f"REQUEST TYPE: {request.object_type}")
    if hasattr(request, 'intent'):
        logger.debug(f"INTENT NAME: {request.intent.name}")
        slots = getattr(request.intent, 'slots', {}) or {}
        for slot_name, slot_obj in slots.items():
            logger.debug(f"  SLOT '{slot_name}' = {slot_obj.value if slot_obj else None}")
    logger.debug(f"SESSION ATTRIBUTES: {json.dumps(session_attr, default=str)}")


def log_response_debug(handler_input, speech):
    session_attr = handler_input.attributes_manager.session_attributes
    logger.debug(f"RESPONSE SPEECH (first 300): {speech[:300]}")
    logger.debug(f"SESSION AFTER: {json.dumps(session_attr, default=str)}")

# ── Prompt helpers ────────────────────────────────────────────────────────────

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

# ── API communication ─────────────────────────────────────────────────────────

def fetch_story(user_id, mode, exclude_ids=None):
    try:
        params = {'user_id': user_id, 'mode': mode}
        if exclude_ids:
            params['exclude_ids'] = ','.join(str(i) for i in exclude_ids)
        url = f"{FETCH_URL}?{urllib.parse.urlencode(params)}"
        logger.info(f"fetch_story: GET {url}")
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
        logger.info(f"fetch_article_chunk: GET {url}")
        req = urllib.request.Request(url, method='GET')
        req.add_header('X-API-Key', ALEXA_API_KEY)
        with urllib.request.urlopen(req, timeout=WEBCALL_TIMEOUT) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            logger.info(f"fetch_article_chunk: has_more={data.get('has_more')} "
                        f"next_offset={data.get('next_offset')} "
                        f"content_len={len(data.get('content',''))}")
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
        logger.info(f"mark_story_interacted: POST user_id={user_id} article_id={article_id} status={status}")
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
            logger.info(f"mark_story_interacted: http={resp.status}")
            return True
    except urllib.error.URLError as e:
        logger.error(f"mark_story_interacted: network error {e}", exc_info=True)
        return False
    except Exception as e:
        logger.error(f"mark_story_interacted: error {e}", exc_info=True)
        return False

# ── Response helpers ──────────────────────────────────────────────────────────

def ask(handler_input, speech, reprompt=None):
    if reprompt is None:
        reprompt = speech
    logger.debug(f"ask(): speech (first 200): {speech[:200]}")
    return (
        handler_input.response_builder
        .speak(speech)
        .ask(reprompt)
        .response
    )

def tell(handler_input, speech):
    logger.debug(f"tell(): speech={speech}")
    return (
        handler_input.response_builder
        .speak(speech)
        .set_should_end_session(True)
        .response
    )

# ── Session helpers ───────────────────────────────────────────────────────────

def _do_mark_read(session_attr, status='read'):
    user_id    = session_attr.get('user_id')
    article_id = session_attr.get('current_article_id')
    logger.info(f"_do_mark_read: article_id={article_id} user_id={user_id} status={status}")
    if user_id and article_id:
        mark_story_interacted(user_id, article_id, status=status)
    else:
        logger.warning(f"_do_mark_read: skipped — missing user_id or article_id")


def is_stop_command(raw):
    result = raw.lower().strip() in ('stop', 'quit', 'exit', 'goodbye', 'bye', 'cancel', 'end', 'finish', 'done')
    logger.debug(f"is_stop_command: raw='{raw}' -> {result}")
    return result


def _is_top_stories(raw):
    return raw.lower() in (
        'top stories', 'best stories', 'top', 'best',
        'personalised', 'personalized', 'switch to top', 'top news',
    )


def _is_latest(raw):
    return raw.lower() in (
        'latest', 'latest news', 'news', 'recent', 'recent news',
        'newest', 'switch to latest',
    )

# ── Core flow functions ───────────────────────────────────────────────────────

def _fetch_and_play(handler_input, session_attr, prefix=""):
    user_id  = session_attr.get('user_id', DEFAULT_USER_ID)
    mode     = session_attr.get('fetch_mode', 'latest')
    seen_ids = session_attr.get('seen_ids', [])
    logger.info(f"_fetch_and_play: user_id={user_id} mode={mode} seen_ids={seen_ids}")

    story = fetch_story(user_id, mode, exclude_ids=seen_ids)

    if not story:
        mode_label = "top" if mode == "top" else "latest"
        speech = f"That's all your {mode_label} stories for now. Check back later!"
        logger.info(f"_fetch_and_play: no story returned, telling end")
        return tell(handler_input, speech)

    article_id = story.get('article_id')
    title      = story.get('title', 'Untitled')

    session_attr['current_article_id'] = article_id
    session_attr['state']              = 'reading'

    seen = session_attr.setdefault('seen_ids', [])
    if article_id not in seen:
        seen.append(article_id)

    logger.info(f"_fetch_and_play: presenting article_id={article_id} title='{title[:80]}'")
    headline = f"{title}. <break time='0.5s'/> {_story_prompt()}"
    speech   = f"{prefix}{headline}" if prefix else headline
    log_response_debug(handler_input, speech)
    return ask(handler_input, speech, reprompt=_story_reprompt())


def _handle_mode_switch(handler_input, session_attr, mode, prefix):
    state = session_attr.get('state', 'reading')
    logger.info(f"_handle_mode_switch: mode={mode} current_state={state}")
    if state == 'reading_article':
        _do_mark_read(session_attr, status='read')
    session_attr['fetch_mode'] = mode
    session_attr['state']      = 'reading'
    return _fetch_and_play(handler_input, session_attr, prefix=prefix)


def _start_reading_article(handler_input, session_attr):
    article_id = session_attr.get('current_article_id')
    logger.info(f"_start_reading_article: article_id={article_id}")

    if not READARTICLE_URL:
        logger.error("_start_reading_article: READARTICLE_URL is not set!")
        return ask(handler_input,
            "Sorry, the article reader is not configured. Want to try the next story?",
            reprompt=_next_story_prompt())

    data = fetch_article_chunk(article_id, offset=0)
    logger.debug(f"_start_reading_article: fetch returned data={data is not None}")

    if not data or not data.get('content'):
        logger.warning(f"_start_reading_article: no content returned for article_id={article_id}")
        return ask(handler_input,
            "Sorry, I couldn't fetch the article content. Want to try the next story?",
            reprompt=_next_story_prompt())

    session_attr['current_article_offset'] = data['next_offset']
    has_more = data.get('has_more', False)
    logger.info(f"_start_reading_article: has_more={has_more} next_offset={data['next_offset']}")

    if has_more:
        session_attr['state'] = 'reading_article'
        speech = f"{data['content']}<break time='1s'/>{_continue_prompt()}"
        log_response_debug(handler_input, speech)
        return ask(handler_input, speech, reprompt=_continue_reprompt())
    else:
        session_attr['state'] = 'after_article'
        speech = f"{data['content']}<break time='1.5s'/>That's the full story. {_next_story_prompt()}"
        log_response_debug(handler_input, speech)
        return ask(handler_input, speech, reprompt=_next_story_reprompt())


def _continue_reading(handler_input, session_attr):
    article_id = session_attr.get('current_article_id')
    offset     = session_attr.get('current_article_offset', 0)
    logger.info(f"_continue_reading: article_id={article_id} offset={offset}")

    data = fetch_article_chunk(article_id, offset=offset)
    logger.debug(f"_continue_reading: fetch returned data={data is not None}")

    if not data or not data.get('content'):
        logger.warning(f"_continue_reading: no content for article_id={article_id} offset={offset}")
        session_attr['state'] = 'after_article'
        return ask(handler_input,
            f"That's all I have. {_next_story_prompt()}",
            reprompt=_next_story_reprompt())

    session_attr['current_article_offset'] = data['next_offset']
    has_more = data.get('has_more', False)
    logger.info(f"_continue_reading: has_more={has_more} next_offset={data['next_offset']}")

    if has_more:
        session_attr['state'] = 'reading_article'
        speech = f"{data['content']}<break time='1s'/>{_continue_prompt()}"
        log_response_debug(handler_input, speech)
        return ask(handler_input, speech, reprompt=_continue_reprompt())
    else:
        session_attr['state'] = 'after_article'
        speech = f"{data['content']}<break time='1.5s'/>That's the end of the article. {_next_story_prompt()}"
        log_response_debug(handler_input, speech)
        return ask(handler_input, speech, reprompt=_next_story_reprompt())

# ── Request handlers ──────────────────────────────────────────────────────────

class LaunchRequestHandler(AbstractRequestHandler):

    def can_handle(self, handler_input):
        return handler_input.request_envelope.request.object_type == "LaunchRequest"

    def handle(self, handler_input):
        logger.info("LaunchRequestHandler: invoked")
        session_attr = handler_input.attributes_manager.session_attributes
        session_attr['user_id']    = DEFAULT_USER_ID
        session_attr['fetch_mode'] = 'latest'
        session_attr['state']      = 'reading'
        session_attr['seen_ids']   = []
        return _fetch_and_play(handler_input, session_attr,
            prefix="MudNews. Here's what's new. ")


class TopStoriesIntentHandler(AbstractRequestHandler):

    def can_handle(self, handler_input):
        return (
            handler_input.request_envelope.request.object_type == "IntentRequest" and
            handler_input.request_envelope.request.intent.name == "TopStoriesIntent"
        )

    def handle(self, handler_input):
        logger.info("TopStoriesIntentHandler: invoked")
        log_request_debug(handler_input)
        session_attr = handler_input.attributes_manager.session_attributes
        if not session_attr.get('user_id'):
            session_attr['user_id']  = DEFAULT_USER_ID
            session_attr['seen_ids'] = []
        return _handle_mode_switch(handler_input, session_attr, 'top',
            prefix="Switching to top stories. ")


class FeedIntentHandler(AbstractRequestHandler):

    def can_handle(self, handler_input):
        if handler_input.request_envelope.request.object_type != "IntentRequest":
            return False
        name = handler_input.request_envelope.request.intent.name
        return name in ("FeedIntent", "AMAZON.YesIntent", "AMAZON.NoIntent")

    def handle(self, handler_input):
        log_request_debug(handler_input)
        session_attr = handler_input.attributes_manager.session_attributes
        state        = session_attr.get('state', 'reading')
        intent_name  = handler_input.request_envelope.request.intent.name

        slots = getattr(handler_input.request_envelope.request.intent, 'slots', {}) or {}
        slot  = slots.get('query')
        raw   = slot.value if slot and slot.value else ''

        logger.info(f"FeedIntentHandler: state={state} intent={intent_name} raw='{raw}'")

        # Ensure session is initialized
        if not session_attr.get('user_id'):
            logger.info("FeedIntentHandler: no user_id in session, initializing")
            session_attr['user_id']    = DEFAULT_USER_ID
            session_attr['fetch_mode'] = 'latest'
            session_attr['state']      = 'reading'
            session_attr['seen_ids']   = []
            return _fetch_and_play(handler_input, session_attr,
                prefix="MudNews. Here's what's new. ")

        # ── Global stop ───────────────────────────────────────────────────────
        if is_stop_command(raw):
            logger.info(f"FeedIntentHandler: stop command detected raw='{raw}'")
            if state == 'reading_article':
                _do_mark_read(session_attr, status='read')
            return tell(handler_input, "See you later!")

        # ── Mode switch ───────────────────────────────────────────────────────
        if _is_top_stories(raw):
            logger.info("FeedIntentHandler: switching to top stories")
            return _handle_mode_switch(handler_input, session_attr, 'top',
                prefix="Switching to top stories. ")
        if _is_latest(raw):
            logger.info("FeedIntentHandler: switching to latest")
            return _handle_mode_switch(handler_input, session_attr, 'latest',
                prefix="Back to latest news. ")

        # ── reading ───────────────────────────────────────────────────────────
        if state == 'reading':
            logger.info(f"FeedIntentHandler: in 'reading' state, intent={intent_name} raw='{raw}'")
            if raw.lower() in ('skip', 'next', 'pass', 'nah'):
                logger.info("FeedIntentHandler: user skipped")
                _do_mark_read(session_attr, status='skipped')
                return _fetch_and_play(handler_input, session_attr)
            if intent_name == "AMAZON.NoIntent" or raw.lower() in ('no', 'nope', 'not interested', 'decline'):
                logger.info("FeedIntentHandler: user declined")
                _do_mark_read(session_attr, status='declined')
                return _fetch_and_play(handler_input, session_attr)
            if intent_name == "AMAZON.YesIntent" or raw.lower() in ('yes', 'yeah', 'yep', 'sure', 'go ahead', 'ok', 'okay', 'read it', 'read'):
                logger.info("FeedIntentHandler: user said yes, starting article read")
                return _start_reading_article(handler_input, session_attr)
            logger.info(f"FeedIntentHandler: unrecognised input in reading state, re-prompting")
            return ask(handler_input, _story_prompt(), reprompt=_story_reprompt())

        # ── reading_article ───────────────────────────────────────────────────
        if state == 'reading_article':
            logger.info(f"FeedIntentHandler: in 'reading_article' state, intent={intent_name} raw='{raw}'")
            if raw.lower() in ('skip', 'next', 'stop reading', 'enough'):
                logger.info("FeedIntentHandler: user skipped mid-article")
                _do_mark_read(session_attr, status='read')
                session_attr['state'] = 'reading'
                return _fetch_and_play(handler_input, session_attr)
            if intent_name == "AMAZON.NoIntent" or raw.lower() in ('no', 'nope', 'stop', "that's enough", 'next story'):
                logger.info("FeedIntentHandler: user said no mid-article")
                _do_mark_read(session_attr, status='read')
                session_attr['state'] = 'reading'
                return _fetch_and_play(handler_input, session_attr)
            if intent_name == "AMAZON.YesIntent" or raw.lower() in ('yes', 'yeah', 'yep', 'sure', 'continue', 'keep going', 'go ahead'):
                logger.info("FeedIntentHandler: user said yes, continuing article")
                return _continue_reading(handler_input, session_attr)
            logger.info(f"FeedIntentHandler: unrecognised input in reading_article state, re-prompting")
            return ask(handler_input, _continue_prompt(), reprompt=_continue_reprompt())

        # ── after_article ─────────────────────────────────────────────────────
        if state == 'after_article':
            logger.info(f"FeedIntentHandler: in 'after_article' state, intent={intent_name} raw='{raw}'")
            if intent_name == "AMAZON.YesIntent" or raw.lower() in ('yes', 'yeah', 'yep', 'sure', 'next', 'go ahead'):
                logger.info("FeedIntentHandler: user wants next story")
                _do_mark_read(session_attr, status='read')
                session_attr['state'] = 'reading'
                return _fetch_and_play(handler_input, session_attr)
            if intent_name == "AMAZON.NoIntent" or raw.lower() in ('no', 'nope', 'done', 'stop', 'finished'):
                logger.info("FeedIntentHandler: user done")
                _do_mark_read(session_attr, status='read')
                return tell(handler_input, "Enjoy your day!")
            logger.info(f"FeedIntentHandler: unrecognised input in after_article state, re-prompting")
            return ask(handler_input, _next_story_prompt(), reprompt=_next_story_reprompt())

        # ── Fallback ──────────────────────────────────────────────────────────
        logger.warning(f"FeedIntentHandler: unrecognised state='{state}', reinitializing")
        session_attr['state'] = 'reading'
        return _fetch_and_play(handler_input, session_attr)


class HelpIntentHandler(AbstractRequestHandler):

    def can_handle(self, handler_input):
        return (
            handler_input.request_envelope.request.object_type == "IntentRequest" and
            handler_input.request_envelope.request.intent.name == "AMAZON.HelpIntent"
        )

    def handle(self, handler_input):
        logger.info("HelpIntentHandler: invoked")
        return ask(handler_input,
            "I'll read you the latest headlines. "
            "Say yes to hear the full article, no to skip to the next story, "
            "or say top stories to switch to your best stories. "
            "Say stop to end at any time.",
            reprompt="Say yes or no to continue, or stop to end.")


class StopIntentHandler(AbstractRequestHandler):

    def can_handle(self, handler_input):
        return (
            handler_input.request_envelope.request.object_type == "IntentRequest" and
            handler_input.request_envelope.request.intent.name in (
                "AMAZON.StopIntent", "AMAZON.CancelIntent"
            )
        )

    def handle(self, handler_input):
        logger.info("StopIntentHandler: invoked")
        log_request_debug(handler_input)
        session_attr = handler_input.attributes_manager.session_attributes
        state = session_attr.get('state')
        logger.info(f"StopIntentHandler: state={state}")
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
        logger.info("FallbackIntentHandler: invoked")
        log_request_debug(handler_input)
        session_attr = handler_input.attributes_manager.session_attributes
        state = session_attr.get('state', 'reading')
        logger.info(f"FallbackIntentHandler: state={state}")
        if state == 'reading':
            return ask(handler_input, _story_prompt(), reprompt=_story_reprompt())
        if state == 'reading_article':
            return ask(handler_input, _continue_prompt(), reprompt=_continue_reprompt())
        if state == 'after_article':
            return ask(handler_input, _next_story_prompt(), reprompt=_next_story_reprompt())
        return ask(handler_input, _story_prompt(), reprompt=_story_reprompt())


class SessionEndedRequestHandler(AbstractRequestHandler):

    def can_handle(self, handler_input):
        return handler_input.request_envelope.request.object_type == "SessionEndedRequest"

    def handle(self, handler_input):
        logger.info("SessionEndedRequestHandler: invoked")
        session_attr = handler_input.attributes_manager.session_attributes
        state = session_attr.get('state')
        logger.info(f"SessionEndedRequestHandler: state={state}")
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
sb.add_request_handler(TopStoriesIntentHandler())
sb.add_request_handler(FeedIntentHandler())
sb.add_request_handler(HelpIntentHandler())
sb.add_request_handler(StopIntentHandler())
sb.add_request_handler(FallbackIntentHandler())
sb.add_request_handler(SessionEndedRequestHandler())
sb.add_exception_handler(CatchAllExceptionHandler())

lambda_handler = sb.lambda_handler()

logger.info("MudNews skill initialised")