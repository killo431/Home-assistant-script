# AI Receptionist — VPS Deployment

Deploys an open-source AI receptionist pipeline on a Linux VPS so it runs
24/7, handles parallel calls, and isn't tied to a local machine. Telephony
providers like Twilio require HTTPS and encrypted WebSockets (WSS) to stream
live call audio, so the VPS uses [Caddy](https://caddyserver.com/) as a
reverse proxy — it fetches and renews Let's Encrypt SSL certificates
automatically.

## Architecture

```
Google Voice -> Twilio -> Caddy (HTTPS/WSS, auto-SSL) -> FastAPI app (Pipecat) -> your OpenAI-compatible LLM provider
```

## Files in this directory

- `server.py` — FastAPI app: a `/voice` webhook that answers the Twilio call,
  and a `/media-stream` WebSocket that runs the real-time Pipecat pipeline
  (Deepgram STT -> your LLM -> Deepgram TTS).
- `requirements.txt` — Python dependencies.
- `.env.example` — required environment variables; copy to `.env` and fill in.
- `Caddyfile.example` — reverse proxy config for automatic HTTPS/WSS.
- `systemd/receptionist.service` — keeps the server running after you log out.

## Step 1: Prepare the server

On a clean Ubuntu/Debian VPS:

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install python3-pip python3-venv curl -y

# Caddy (for automatic production SSL)
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update
sudo apt install caddy -y
```

## Step 2: Point a domain at the VPS and configure Caddy

Create an A record (e.g. `ai.yourdomain.com`) pointing at your VPS's public
IP with your DNS provider (Cloudflare, Namecheap, etc.), then:

```bash
sudo cp Caddyfile.example /etc/caddy/Caddyfile
sudo nano /etc/caddy/Caddyfile   # replace ai.yourdomain.com with your domain
sudo systemctl restart caddy
```

Caddy will fetch and manage the SSL certificate automatically.

## Step 3: Set up the Python app

```bash
mkdir -p ~/ai-receptionist-vps && cd ~/ai-receptionist-vps
# copy server.py, requirements.txt, and .env.example into this directory
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
nano .env   # fill in TWILIO_*, DEEPGRAM_API_KEY, PROVIDER_API_KEY, PUBLIC_HOSTNAME
chmod 600 .env
```

Run it once in the foreground to confirm it starts:

```bash
uvicorn server:app --host 127.0.0.1 --port 8000
```

## Step 4: Keep it running with systemd

```bash
sudo cp systemd/receptionist.service /etc/systemd/system/receptionist.service
sudo nano /etc/systemd/system/receptionist.service
# set User= and the two paths (WorkingDirectory, ExecStart) to match your VPS user
sudo systemctl daemon-reload
sudo systemctl enable --now receptionist
```

Secrets live in `.env` (loaded via `EnvironmentFile=`), not in the unit file
itself, so `systemctl cat receptionist` won't leak API keys.

Watch live logs while testing:

```bash
sudo journalctl -u receptionist -f
```

## Step 5: Hook it up to Twilio

1. Log into the Twilio Console -> **Active Numbers** -> your number.
2. Under **Voice & Fax** -> **A CALL COMES IN**, choose **Webhook**, set the
   URL to `https://ai.yourdomain.com/voice`, method **HTTP POST**.
3. Save.

## Testing

Call your Google Voice / Twilio number. Twilio POSTs to `/voice`, gets back
TwiML pointing at `wss://ai.yourdomain.com/media-stream`, and Caddy proxies
that encrypted stream straight through to the FastAPI WebSocket, where
Pipecat runs speech-to-text -> your LLM -> text-to-speech in real time.

## Notes

- `server.py` targets `pipecat-ai>=1.4.0`. Pipecat's API moves quickly across
  major versions — if you pin an older release, check the
  [Pipecat docs](https://docs.pipecat.ai/) for the matching import paths.
- Swap `DeepgramSTTService`/`DeepgramTTSService` or the LLM provider for any
  other Pipecat-supported service as needed.
