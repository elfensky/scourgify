#!/usr/bin/env python3
"""Pins the engine seam (engines.py): the cloud adapters against a fake transport (_post_json is
the seam — no network), and the Gemini blocked-content RuntimeError that ask_retry's no-retry
branch keys off. No framework:  uv run tests/test_engines.py   (also pytest-collectable)."""
import os, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from scourgify import common, engines


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
        assert out == "" and err.startswith("blocked:")
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
