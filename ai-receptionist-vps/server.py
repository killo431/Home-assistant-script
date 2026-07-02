import json
import os

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket
from fastapi.responses import Response

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.serializers.twilio import TwilioFrameSerializer
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.deepgram.tts import DeepgramTTSService
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.transports.websocket.fastapi import FastAPIWebsocketParams, FastAPIWebsocketTransport

load_dotenv()

PUBLIC_HOSTNAME = os.getenv("PUBLIC_HOSTNAME", "ai.yourdomain.com")
SYSTEM_PROMPT = os.getenv(
    "SYSTEM_PROMPT",
    "You are a professional business receptionist. Answer questions concisely.",
)

app = FastAPI()


@app.post("/voice")
async def handle_voice_call():
    """Webhook Twilio hits when a call comes in; opens a media stream back to us."""
    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Connect>
        <Stream url="wss://{PUBLIC_HOSTNAME}/media-stream" />
    </Connect>
</Response>"""
    return Response(content=twiml, media_type="application/xml")


@app.websocket("/media-stream")
async def media_stream_endpoint(websocket: WebSocket):
    await websocket.accept()

    # Twilio sends a "connected" event, then a "start" event, before any media
    # frames. "start" carries the streamSid/callSid the serializer needs.
    messages = websocket.iter_text()
    await messages.__anext__()
    start_data = json.loads(await messages.__anext__())
    stream_sid = start_data["start"]["streamSid"]
    call_sid = start_data["start"]["callSid"]

    serializer = TwilioFrameSerializer(
        stream_sid=stream_sid,
        call_sid=call_sid,
        account_sid=os.getenv("TWILIO_ACCOUNT_SID", ""),
        auth_token=os.getenv("TWILIO_AUTH_TOKEN", ""),
    )

    transport = FastAPIWebsocketTransport(
        websocket=websocket,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            add_wav_header=False,
            serializer=serializer,
        ),
    )

    # Any OpenAI-compatible provider (DeepSeek, Groq, etc.) via base_url
    llm = OpenAILLMService(
        api_key=os.getenv("PROVIDER_API_KEY"),
        base_url=os.getenv("PROVIDER_BASE_URL", "https://api.deepseek.com/v1"),
        model=os.getenv("PROVIDER_MODEL", "deepseek-chat"),
    )

    stt = DeepgramSTTService(api_key=os.getenv("DEEPGRAM_API_KEY"))
    tts = DeepgramTTSService(
        api_key=os.getenv("DEEPGRAM_API_KEY"),
        voice=os.getenv("DEEPGRAM_VOICE", "aura-asteria-en"),
    )

    context = LLMContext([{"role": "system", "content": SYSTEM_PROMPT}])
    aggregators = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(vad_analyzer=SileroVADAnalyzer()),
    )

    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            aggregators.user(),
            llm,
            tts,
            transport.output(),
            aggregators.assistant(),
        ]
    )

    task = PipelineTask(
        pipeline,
        params=PipelineParams(audio_in_sample_rate=8000, audio_out_sample_rate=8000),
    )

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        await task.cancel()

    runner = PipelineRunner(handle_sigint=False)
    await runner.run(task)
