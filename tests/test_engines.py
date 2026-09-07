#!/usr/bin/env python3
"""Pins the engine seam (engines.py): the cloud adapters against a fake transport (_post_json is
the seam — no network), and the Gemini blocked-content RuntimeError that ask_retry's no-retry
branch keys off. No framework:  uv run tests/test_engines.py   (also pytest-collectable)."""
import contextlib, os, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from scourgify import common, engines


@contextlib.contextmanager
def _as_platform(name):
    """Monkeypatch sys.platform (via engines' own `import sys`, the real module — a singleton) for
    the block, restoring after. Mirrors tests/test_paths.py's _as_windows() os.name idiom."""
    real = engines.sys.platform
    engines.sys.platform = name
    try:
        yield
    finally:
        engines.sys.platform = real


def _with_transport(resp, fn):
    """Run fn with _post_json replaced by a canned response; restore after."""
    real = engines._post_json
    calls = []
    engines._post_json = lambda url, headers, payload, timeout: (calls.append((url, headers, payload)), resp)[1]
    try:
        return fn(), calls
    finally:
        engines._post_json = real


_KEYS = ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "MISTRAL_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY")


def _saved_env():
    return {k: os.environ.get(k) for k in _KEYS}


def _restore_env(saved):
    for k, v in saved.items():
        os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


def test_chat_adapters_extract_their_responses():
    saved = _saved_env()
    os.environ["OPENAI_API_KEY"] = "sk-t"; os.environ["ANTHROPIC_API_KEY"] = "sk-t"; os.environ["MISTRAL_API_KEY"] = "sk-t"
    try:
        out, calls = _with_transport({"choices": [{"message": {"content": "hi"}}]},
                                     lambda: engines.OpenAI("", 60).ask("p"))
        assert out == "hi" and "openai.com" in calls[0][0] and calls[0][1]["Authorization"] == "Bearer sk-t"
        out, calls = _with_transport({"content": [{"text": "hello"}]},
                                     lambda: engines.Claude("", 60).ask("p"))
        assert out == "hello" and calls[0][1]["x-api-key"] == "sk-t"       # claude: header auth + its own extract path
        out, _ = _with_transport({"choices": [{"message": {"content": "salut"}}]},
                                 lambda: engines.Mistral("", 60).ask("p"))
        assert out == "salut"
    finally:
        _restore_env(saved)


def test_gemini_blocked_content_is_a_no_retry_runtimeerror():
    saved = _saved_env()
    os.environ["GEMINI_API_KEY"] = "g-t"
    try:
        # blocked prompt -> RuntimeError("blocked:...") -> ask_retry returns immediately, no retries
        def blocked():
            return engines.Gemini("", 60).ask("p")
        try:
            _with_transport({"promptFeedback": {"blockReason": "PROHIBITED_CONTENT"}}, blocked)
            assert False, "expected RuntimeError for blocked content"
        except RuntimeError as e:
            assert str(e).startswith("blocked:PROHIBITED_CONTENT")
        # and the signal survives through ask_retry's no-retry branch
        class G:
            def ask(self, p): raise RuntimeError("blocked:PROHIBITED_CONTENT")
        out, err = engines.ask_retry(G(), "p")
        assert out == "" and engines.failure_class(err) == engines.REFUSAL and "blocked:" in err
        # empty candidates -> nocontent
        out, _ = _with_transport({"candidates": [{"content": {"parts": [{"text": "ok"}]}}]},
                                 lambda: engines.Gemini("", 60).ask("p"))
        assert out == "ok"
    finally:
        _restore_env(saved)


def test_missing_key_message_names_the_env_var():
    saved = _saved_env()
    try:
        for cls, env in ((engines.OpenAI, "OPENAI_API_KEY"), (engines.Claude, "ANTHROPIC_API_KEY"),
                         (engines.Mistral, "MISTRAL_API_KEY")):
            os.environ.pop(env, None)
            try:
                cls("", 60); assert False, f"expected a refusal for {env}"
            except common.GuardrailError as e:
                assert env in str(e)
    finally:
        _restore_env(saved)


def test_engine_tables_agree():
    assert set(engines.ENGINE_ENV) == set(engines.ENGINES) - {"apple"}   # every cloud engine has a key entry
    assert set(engines.PRICING) == set(engines.ENGINES)                  # ... and a price row


def test_apple_is_absent_off_its_platform():
    """XPLAT-02: apple never appears off its platform, even when the afm/swift probe would have
    succeeded — the platform gate runs FIRST."""
    with _as_platform("linux"):
        assert "apple" not in engines.usable_engines(env={})
    with _as_platform("win32"):
        assert "apple" not in engines.usable_engines(env={})


def test_apple_is_still_absent_in_platform_without_a_toolchain():
    """The platform gate COMPOSES with the existing afm/swift probe, it does not replace it: an
    in-platform host with neither the binary nor a toolchain still yields no apple."""
    import shutil
    real_which, real_shipped = shutil.which, engines._shipped_dir
    shutil.which = lambda name: None
    engines._shipped_dir = lambda: os.path.join(os.sep, "no", "such", "shipped", "dir")
    try:
        with _as_platform("darwin"):
            assert "apple" not in engines.usable_engines(env={})
    finally:
        shutil.which = real_which
        engines._shipped_dir = real_shipped


def test_cloud_engines_are_unaffected_by_the_platform_gate():
    with _as_platform("win32"):
        assert "openai" in engines.usable_engines(env={"OPENAI_API_KEY": "sk-t"})
    with _as_platform("linux"):
        assert "claude" in engines.usable_engines(env={"ANTHROPIC_API_KEY": "sk-t"})


def test_the_platform_gate_is_a_trait_not_a_name_test():
    """A newly registered engine with no `platforms` row is unconstrained without any code change
    — constraining an engine is a row edit, never a `== "apple"` scattered across the tools."""
    plats = engines.trait("apple", "platforms")
    assert plats and "darwin" in plats and "linux" not in plats
    assert engines.trait("openai", "platforms") is None          # falls back to the permissive default
    assert engines.trait("some-new-engine", "platforms") is None


def test_apple_is_absent_on_this_host_unless_it_is_darwin():
    """Patches NOTHING — the only assertion in this file that reads the REAL sys.platform, so
    plan 01-02's test-windows job (which runs the whole tests/test_*.py glob) proves ROADMAP
    success criterion 5 against a genuinely non-darwin host, not only a monkeypatched one. The
    four tests above prove the LOGIC, which is identical on any host; this one proves the FACT on
    whichever host actually runs it."""
    if sys.platform in engines.trait("apple", "platforms"):
        print("  (this host is on apple's platform list — logic already proven by the tests above)")
        return
    assert "apple" not in engines.usable_engines(env={})


def test_traits_free_workers_judge():
    """Engine traits are data, not name string-tests: free-ness derives from PRICING (one source
    of truth), parallelism caps workers, judge-capability drives promote's default/warning."""
    assert engines.is_free("apple") is True
    assert engines.is_free("claude") is False
    assert engines.is_free("nonexistent") is False        # unknown engine: assume it costs
    assert engines.max_workers("apple", 8) == 1           # one subprocess pipe, not thread-safe
    assert engines.max_workers("claude", 8) == 8
    assert engines.trait("apple", "judge") is False
    assert engines.trait("claude", "judge") is True
    for e in engines.ENGINES:                             # every engine has both hint halves
        assert engines.trait(e, "hint") and engines.trait(e, "unusable")


def test_every_engine_states_a_role_and_how_it_will_let_you_down():
    """The settings dialog and the engine picker DERIVE their rows from TRAITS (NLSpec B4.2), so a
    newly registered engine with no `role`/`limits` would render a blank row that reads as "nothing
    to say about this one" rather than "nobody wrote it down"."""
    for e in engines.ENGINES:
        assert engines.trait(e, "role"), f"{e} has no role line"
        assert len(engines.trait(e, "limits")) > 30, f"{e} does not say how it will let you down"
    assert engines.trait("gemini", "refuses") is True     # the one the menu reads as behaviour
    assert engines.trait("openai", "refuses") is False


def test_env_beats_stored_keys_and_the_row_can_say_so():
    """NLSpec B5.2 / B5 edge case. This is the OPPOSITE of the library path's rule
    (common.set_library beats $CALIBRE_LIBRARY): a key is user config, so an exported one must keep
    a scripted run working; the library path is a fact about the host process. Do not harmonize."""
    stored = {"openai": "sk-stored-openai-key", "claude": "sk-stored-claude-key"}
    keys = engines.resolve_keys(stored, env={"OPENAI_API_KEY": "sk-env-openai-key"})
    assert keys["OPENAI_API_KEY"] == "sk-env-openai-key"          # env wins where both exist
    assert keys["ANTHROPIC_API_KEY"] == "sk-stored-claude-key"    # stored fills in where env is silent
    assert "MISTRAL_API_KEY" not in keys                          # neither -> absent, not empty
    assert engines.key_source("openai", stored, env={"OPENAI_API_KEY": "x"}) == "env"
    assert engines.key_source("claude", stored, env={}) == "stored"
    assert engines.key_source("mistral", stored, env={}) == ""
    # gemini's second env name resolves onto the name every constructor reads first
    assert engines.resolve_keys({}, env={"GOOGLE_API_KEY": "g-t"})["GEMINI_API_KEY"] == "g-t"


def test_stored_keys_make_an_engine_usable_exactly_as_env_keys_do():
    """#58's first acceptance line. usable_engines already took an injected mapping; resolve_keys
    is what produces that mapping from a settings store, so the GUI and a shell agree."""
    assert "claude" not in engines.usable_engines(env=engines.resolve_keys({}, env={}))
    keys = engines.resolve_keys({"claude": "sk-stored"}, env={})
    assert "claude" in engines.usable_engines(env=keys)


def test_a_key_reaches_an_engine_without_touching_the_environment():
    """The seam that makes the settings dialog more than decoration: before this, every constructor
    read os.environ directly, so "keys from plugin JSON" was impossible without mutating the
    environment — which two concurrent jobs and a CLI alongside would then observe (B5.2)."""
    saved = _saved_env()
    try:
        for k in _KEYS: os.environ.pop(k, None)
        keys = engines.resolve_keys({"openai": "sk-from-the-settings-dialog"}, env={})
        out, calls = _with_transport({"choices": [{"message": {"content": "hi"}}]},
                                     lambda: engines.OpenAI("", 60, env=keys).ask("p"))
        assert out == "hi" and calls[0][1]["Authorization"] == "Bearer sk-from-the-settings-dialog"
        assert os.environ.get("OPENAI_API_KEY") is None, "the environment must be left alone"
        assert engines.Gemini("", 60, env={"GOOGLE_API_KEY": "g-t"}).key == "g-t"
        assert engines.ENGINES["mistral"]("", 60, env={"MISTRAL_API_KEY": "m"}).key == "m"
    finally:
        _restore_env(saved)


def test_failures_are_classified_so_the_menu_knows_which_retry_to_offer():
    """NLSpec B3.6. B1 offers "Retry on <other engine>" ONLY for a refusal — every other class is
    the same engine's problem, and retrying a 401 elsewhere is the same 401 plus a wasted click."""
    import urllib.error
    def http(code):
        return urllib.error.HTTPError("https://api.example/v1", code, "nope", {}, None)
    assert engines.classify_error(http(401)) == engines.AUTH
    assert engines.classify_error(http(403)) == engines.PERMISSION
    assert engines.classify_error(http(429)) == engines.QUOTA
    assert engines.classify_error(http(500)) == engines.ERROR
    assert engines.classify_error(TimeoutError("timed out")) == engines.TIMEOUT
    assert engines.classify_error(urllib.error.URLError(TimeoutError("timed out"))) == engines.TIMEOUT
    assert engines.classify_error(RuntimeError("blocked:PROHIBITED_CONTENT")) == engines.REFUSAL
    assert engines.classify_error(KeyError("choices")) == engines.PARSE       # _extract on a shape it didn't expect
    # ... and it survives the round trip through the one shared `reason` column
    class Boom:
        def __init__(self, exc): self.exc = exc
        def ask(self, p): raise self.exc
    for exc, want in ((http(401), engines.AUTH), (http(403), engines.PERMISSION),
                      (RuntimeError("blocked:x"), engines.REFUSAL), (KeyError("choices"), engines.PARSE)):
        _, reason = engines.ask_retry(Boom(exc), "p", tries=1)
        assert engines.failure_class(reason) == want, reason
    assert engines.failure_class("HTTPError: something old") == engines.ERROR   # pre-taxonomy row
    assert engines.failure_class("") == engines.ERROR


def test_a_bad_key_fails_fast_instead_of_backing_off_four_times():
    """A behaviour change worth stating: ask_retry used to retry everything but a RuntimeError, so a
    401 cost ~14 s of backoff to arrive at the same answer. Only RETRYABLE classes back off now."""
    import urllib.error
    tries = []
    class Boom:
        def ask(self, p):
            tries.append(1)
            raise urllib.error.HTTPError("https://api.example/v1", 401, "nope", {}, None)
    engines.ask_retry(Boom(), "p", tries=4)
    assert len(tries) == 1, "a 401 must not be retried"
    assert engines.REFUSAL not in engines.RETRYABLE and engines.AUTH not in engines.RETRYABLE
    assert engines.QUOTA in engines.RETRYABLE and engines.TIMEOUT in engines.RETRYABLE


def test_no_key_ever_reaches_a_recorded_failure_reason():
    """NLSpec B5 postcondition. A failure reason lands in a CSV, a job log and an error dialog, and
    an HTTPError's message can echo request context — so the redaction is at the one place every
    reason is built, not at each of those three."""
    secret = "sk-proj-supersecret-key-value-1234"
    class Leaky:
        key = secret
        def ask(self, p): raise ValueError(f"request failed with Authorization: Bearer {secret}")
    _, reason = engines.ask_retry(Leaky(), "p", tries=1)
    assert secret not in reason and "<key>" in reason
    assert engines.redact("nothing to hide", None, "") == "nothing to hide"   # no secret, no damage
    assert engines.mask(secret) == "sk-proj-••••••••••••1234" and secret not in engines.mask(secret)
    assert engines.mask("") == ""


def test_an_untouched_settings_field_keeps_its_key_and_an_emptied_one_clears_it():
    """The settings field shows the MASK, so the obvious save-what-is-typed would store twelve
    bullets as the API key the first time someone clicks OK without retyping — a silent break that
    only shows up as an auth failure much later."""
    stored = "sk-proj-supersecret-key-value-1234"
    assert engines.unmask(engines.mask(stored), stored) == stored     # untouched -> unchanged
    assert engines.unmask("", stored) == ""                           # cleared -> cleared
    assert engines.unmask("  sk-new-key  ", stored) == "sk-new-key"   # retyped -> the new key
    assert engines.unmask("", "") == ""


def test_est_cost_prices_a_reasoning_engine_by_its_thinking_tokens():
    """A reasoning model bills hidden thinking as OUTPUT. Measured against real library books
    (2026-07-30): gemini-2.5-flash returns ~50 answer tokens on top of ~1061 thinking tokens, so a
    full-library pass really costs ~$24.51 — the flat-80 estimate quoted $4.59, a fifth of it, in
    the wizard's confirm right before the user spends. Under-quoting is the dangerous direction."""
    from scourgify import classify
    assert engines.trait("gemini", "out_tokens") > 1000        # thinking is counted
    assert engines.trait("openai", "out_tokens") == 80         # non-reasoning default unchanged
    gem = classify.est_cost(7949, "gemini")
    assert 20 < gem < 30, gem                                  # measured $24.51 on a 5-book sample
    assert classify.est_cost(7949, "apple") == 0               # free engines stay free
    # ordering the wizard's engine table depends on: gemini must not look cheaper than openai
    assert gem > classify.est_cost(7949, "openai")


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    for n, f in fns:
        f(); print(f"  ok {n}")
    print(f"{len(fns)} tests passed")
