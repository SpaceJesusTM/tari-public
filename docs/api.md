# Local API for a trusted backend

Tari uses OpenClaw's existing Gateway HTTP endpoint. The configuration enables
`gateway.http.endpoints.chatCompletions.enabled`; the host port defaults to 18899.
It requires `Authorization: Bearer <installation gateway token>`.

```http
POST /v1/chat/completions
Authorization: Bearer <your private token>
Content-Type: application/json

{"model":"openclaw/tari","user":"demo-thread-1","messages":[{"role":"user","content":"What can you help with?"}]}
```

Read the response at `choices[0].message.content`. A stable `user` value continues
the agent's session; use a distinct opaque ID for each conversation. The `model`
field selects the agent, not the foundation model. Provider/model choice lives in
the private OpenClaw configuration. OpenClaw also supports streaming with
`"stream": true`; the included CLI/example uses non-streaming responses.

```sh
export TARI_GATEWAY_TOKEN="$(./tari token)"
python3 examples/client.py --conversation demo-thread-1 "Hello Tari"
python3 examples/client.py --conversation demo-thread-1 "What did I just say?"
unset TARI_GATEWAY_TOKEN
```

Only run this on your trusted local machine/backend. A shared Gateway token grants
operator authority; it is not a restricted end-user API key. Do not bundle it into
browser JavaScript, send it to a voice provider, or expose this port publicly. A
future web app needs its own user authentication, conversation isolation, and a
server-side request policy before forwarding owner-authorized requests. Do not
give a voice frontend direct filesystem or OAuth access.

Treat tool outputs, email, documents, and voice transcripts from third parties as
untrusted content. Typed task tools can be added later with their own validation
and authorization; this project does not yet expose a separate narrow task API.

Contract checked against the [pinned OpenClaw HTTP API documentation](https://github.com/openclaw/openclaw/blob/v2026.9.9/docs/gateway/openai-http-api.md).
