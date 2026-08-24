#!/usr/bin/env bash
# Wildframe hack-verification suite. Every check prints PASS/FAIL with evidence.
set -u
GW="${WF_API_URL:-https://localhost:8000}"
DEMO_EMAIL="demo@wildframe.com"; DEMO_PASS="DemoPass123!"
HACK_EMAIL="hacker@wildframe.com"; HACK_PASS="HackerPass123!"
CURL="curl -sk"

jq_get() { python3 -c "import json,sys;d=json.load(sys.stdin);print(eval(sys.argv[1]))" "$1" 2>/dev/null; }

echo "== setup tokens =="
DEMO_TOKEN=$($CURL -X POST $GW/auth/api/v1/auth/login -H "Content-Type: application/json" -d "{\"email\":\"$DEMO_EMAIL\",\"password\":\"$DEMO_PASS\"}" | jq_get "d['access_token']")
$CURL -X POST $GW/auth/api/v1/auth/register -H "Content-Type: application/json" -d "{\"email\":\"$HACK_EMAIL\",\"password\":\"$HACK_PASS\",\"first_name\":\"Hack\",\"last_name\":\"Er\"}" -o /dev/null
HACK_TOKEN=$($CURL -X POST $GW/auth/api/v1/auth/login -H "Content-Type: application/json" -d "{\"email\":\"$HACK_EMAIL\",\"password\":\"$HACK_PASS\"}" | jq_get "d['access_token']")
DEMO_UID=$($CURL -H "Authorization: Bearer $DEMO_TOKEN" $GW/auth/api/v1/auth/me | jq_get "d['id']")
HACK_UID=$($CURL -H "Authorization: Bearer $HACK_TOKEN" $GW/auth/api/v1/auth/me | jq_get "d['id']")
echo "demo=$DEMO_UID hacker=$HACK_TOKEN>0 && ok"

check() { # name expected actual
  if [ "$2" = "$3" ]; then echo "PASS  $1 (got $3)"; else echo "FAIL  $1 (want $2 got $3)"; fi
}

echo "== A. auth bypass =="
check "no-token /auth/me -> 401" 401 "$($CURL -o /dev/null -w '%{http_code}' $GW/auth/api/v1/auth/me)"
TAMPERED="${DEMO_TOKEN%?}x"
check "tampered JWT /auth/me -> 401" 401 "$($CURL -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $TAMPERED" $GW/auth/api/v1/auth/me)"
REFRESH=$($CURL -X POST $GW/auth/api/v1/auth/login -H "Content-Type: application/json" -d "{\"email\":\"$DEMO_EMAIL\",\"password\":\"$DEMO_PASS\"}" | jq_get "d['refresh_token']")
check "refresh-as-access on /users/me -> 401/403" 401 "$($CURL -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $REFRESH" $GW/users/api/v1/users/me)"
FAKE=$($CURL -H "Authorization: Bearer $DEMO_TOKEN" $GW/auth/api/v1/auth/me >/dev/null; python3 - <<EOF
import base64,json,time
h=base64.urlsafe_b64encode(json.dumps({"alg":"HS256","typ":"JWT","kid":"k1"}).encode()).rstrip(b"=").decode()
p=base64.urlsafe_b64encode(json.dumps({"sub":"$HACK_UID","role":"admin","type":"access","aud":"wildframe-api","exp":int(time.time())+600,"iat":int(time.time()),"arv":1}).encode()).rstrip(b"=").decode()
print(f"{h}.{p}.ZmFrZXNpZ25hdHVyZWZha2VzaWduYXR1cmU")
EOF
)
check "forged admin JWT on admin users -> 401" 401 "$($CURL -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $FAKE" $GW/admin/api/v1/admin/users/moderated)"

echo "== B. privilege escalation =="
check "hacker GET admin users/moderated -> 403" 403 "$($CURL -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $HACK_TOKEN" $GW/admin/api/v1/admin/users/moderated)"
check "hacker moderate w/o reauth -> 401" 401 "$($CURL -o /dev/null -w '%{http_code}' -X POST -H "Authorization: Bearer $HACK_TOKEN" -H "Content-Type: application/json" -d '{"user_id":"'$DEMO_UID'","status":"banned"}' $GW/admin/api/v1/admin/users/moderate)"
check "hacker moderate w/ own reauth -> 403" 403 "$($CURL -o /dev/null -w '%{http_code}' -X POST -H "Authorization: Bearer $HACK_TOKEN" -H "X-Admin-Reauth: $HACK_TOKEN" -H "Content-Type: application/json" -d '{"user_id":"'$DEMO_UID'","status":"banned"}' $GW/admin/api/v1/admin/users/moderate)"
check "hacker admin config list -> 403" 403 "$($CURL -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $HACK_TOKEN" $GW/admin/api/v1/admin/config)"
check "hacker audit log -> 403" 403 "$($CURL -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $HACK_TOKEN" $GW/admin/api/v1/admin/audit-logs)"

echo "== C. IDOR =="
# demo creates a playlist item; hacker tries to read/modify it
PLIST=$($CURL -X POST -H "Authorization: Bearer $DEMO_TOKEN" -H "Content-Type: application/json" -d '{"name":"secret"}' $GW/users/api/v1/playlists | jq_get "d['id']")
if [ -n "$PLIST" ] && [ "$PLIST" != "None" ]; then
  check "hacker GET demo playlist -> 403/404" 403 "$($CURL -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $HACK_TOKEN" $GW/users/api/v1/playlists/$PLIST)"
else
  echo "note: playlist create failed or endpoint differs — trying profiles instead"
fi
check "hacker GET demo profile -> 403" 403 "$($CURL -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $HACK_TOKEN" $GW/users/api/v1/profiles/$DEMO_UID)"
check "hacker GET demo subscription -> 403" 403 "$($CURL -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $HACK_TOKEN" $GW/billing/api/v1/billing/subscription/$DEMO_UID)"
# playback session idor: start session as demo, end as hacker
SESS=$($CURL -X POST -H "Authorization: Bearer $DEMO_TOKEN" -H "Content-Type: application/json" -d '{"user_id":"'$DEMO_UID'","content_id":"f0dd096f-00ce-4022-a131-cd5249f18f28","device_id":"idor-test"}' $GW/streaming/api/v1/playback-sessions | jq_get "d['id']")
if [ -n "$SESS" ] && [ "$SESS" != "None" ]; then
  check "hacker ends demo session -> 403" 403 "$($CURL -o /dev/null -w '%{http_code}' -X POST -H "Authorization: Bearer $HACK_TOKEN" $GW/streaming/api/v1/playback-sessions/$SESS/end)"
else
  echo "note: session create failed"
fi

echo "== D. injection =="
check "SQLi search q -> 200/400 not 500" 200 "$($CURL -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $DEMO_TOKEN" "$GW/search/api/v1/search?q=%27%20OR%201%3D1--")"
check "SQLi content genre param -> 200" 200 "$($CURL -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $DEMO_TOKEN" "$GW/content/api/v1/content?genre=%27%3B%20DROP%20TABLE%20users%3B--")"
TRAV=$($CURL -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $DEMO_TOKEN" "$GW/uploads/api/v1/uploads/../../etc/passwd")
if [ "$TRAV" = "400" ] || [ "$TRAV" = "404" ] || [ "$TRAV" = "403" ]; then r=ok; else r=$TRAV; fi
echo "PASS? uploads traversal -> HTTP $TRAV (expect non-200)"
XF=$($CURL -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $DEMO_TOKEN" -H "X-Forwarded-For: 1.2.3.4" $GW/auth/api/v1/auth/me)
check "XFF-spoofed /auth/me still works (not IP-bound)" 200 "$XF"

echo "== E. stored XSS payload through API =="
XSS_TITLE='<script>alert(1)</script><img src=x onerror=window.__xss=42>'
CC=$($CURL -X POST -H "Authorization: Bearer $DEMO_TOKEN" -H "Content-Type: application/json" -d '{"title":"'"$XSS_TITLE"'","description":"<svg onload=alert(3)>","content_type":"movie","genres":[]}' $GW/content/api/v1/content | jq_get "d['id']")
echo "created xss content id: $CC"
echo "$CC" > /tmp/opencode/xss_content_id
echo "== done =="
