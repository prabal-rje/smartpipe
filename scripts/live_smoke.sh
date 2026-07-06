#!/usr/bin/env bash
# Owner-run live validity smoke (plan/post-1.1/10). NEVER wired into CI or gates.
# Small models, tiny inputs, --max-calls belts everywhere; keys are checked for
# presence only and never printed. Refuses to run without the deliberate speed bump:
#   SEMPIPE_LIVE_SMOKE=yes make live-smoke
set -u

if [ "${SEMPIPE_LIVE_SMOKE:-}" != "yes" ]; then
  echo "refusing: set SEMPIPE_LIVE_SMOKE=yes to spend real API calls" >&2
  exit 64
fi
[ -f .env ] && set -a && . ./.env && set +a

pass=0; fail=0; skip=0
cell() { # name, key-var, command...
  local name="$1" key="$2"; shift 2
  if [ -z "${!key:-}" ]; then printf '%-34s – (no %s)\n' "$name" "$key"; skip=$((skip+1)); return; fi
  if out=$(eval "$@" 2>/dev/null) && [ -n "$out" ]; then
    printf '%-34s ✓ %s\n' "$name" "$(echo "$out" | head -1 | cut -c1-40)"; pass=$((pass+1))
  else
    printf '%-34s ✗\n' "$name"; fail=$((fail+1))
  fi
}

echo "sempipe live smoke — $(date +%F) — HEAD $(git rev-parse --short HEAD)"
cell "openai map"          OPENAI_API_KEY     'echo bonjour | SEMPIPE_MODEL=gpt-4o-mini uv run sempipe map "translate to English, one word" --max-calls 2'
cell "openai extract"      OPENAI_API_KEY     'echo "hola amigo" | SEMPIPE_MODEL=gpt-4o-mini uv run sempipe map "Extract {greeting, language}" --max-calls 2'
cell "openai embed+top_k"  OPENAI_API_KEY     'printf "invoice overdue\nnice weather\n" | SEMPIPE_EMBED_MODEL=text-embedding-3-small uv run sempipe embed --max-calls 4 | SEMPIPE_EMBED_MODEL=text-embedding-3-small uv run sempipe top_k 1 --near "billing" --max-calls 2'
cell "anthropic map"       ANTHROPIC_API_KEY  'echo "hola mundo" | SEMPIPE_MODEL=claude-haiku-4-5 uv run sempipe map "translate to English, two words" --max-calls 2'
cell "mistral map"         MISTRAL_API_KEY    'echo "guten tag" | SEMPIPE_MODEL=mistral-small-latest uv run sempipe map "translate to English, two words" --max-calls 2'
cell "mistral embed"       MISTRAL_API_KEY    'echo hello | SEMPIPE_EMBED_MODEL=mistral-embed uv run sempipe embed --max-calls 2'
cell "voxtral hears"       MISTRAL_API_KEY    'uv run python -c "import math,struct,wave;w=wave.open(\"/tmp/smoke-beep.wav\",\"w\");w.setnchannels(1);w.setsampwidth(2);w.setframerate(16000);w.writeframes(b\"\".join(struct.pack(\"<h\",int(9000*math.sin(2*math.pi*440*t/16000))) for t in range(16000)));w.close()" && SEMPIPE_MODEL=voxtral-mini-latest uv run sempipe map "Describe the sound in three words" --in /tmp/smoke-beep.wav --max-calls 2 </dev/null'
cell "gemini map"          GEMINI_API_KEY     'echo bonjour | SEMPIPE_MODEL=gemini-2.5-flash-lite uv run sempipe map "translate to English, one word" --max-calls 2'
cell "gemini embed"        GEMINI_API_KEY     'echo hello | SEMPIPE_EMBED_MODEL=gemini/gemini-embedding-001 uv run sempipe embed --max-calls 2'
cell "openrouter map"      OPENROUTER_API_KEY 'echo ciao | SEMPIPE_MODEL=openrouter/openai/gpt-4o-mini uv run sempipe map "translate to English, one word" --max-calls 2'

echo "----"
echo "pass=$pass fail=$fail skipped=$skip  (append this row to MAINTENANCE.md)"
[ "$fail" -eq 0 ]
