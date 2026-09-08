# Speaking Feature Improvements & Status

This document summarizes the current state of the AI speaking feature across the frontend and backend, as well as the necessary steps to implement real AI grading and live conversational capabilities.

## 1. Current State of the AI Speaking Flow

The speaking flow is fully structured but currently operates using a **deterministic mock backend**. It does not utilize any actual AI models.

### Frontend (`english-learning-web`)
- Users are presented with a recording UI utilizing the browser's `MediaRecorder` API.
- Upon completion, the audio is converted to a base64 Data URL.
- Submitting the exercise (either as an assignment or for instant AI practice) sends the `audio_recording_url` to the backend.

### Backend (`english-learning-api`)
- The API routes the submission to `core.ai.service.transcribe_and_score_speaking`.
- By default, the system uses a `MockBackend` which completely ignores the audio content.
- The `MockBackend` generates a deterministic score based on the length of the `audio_recording_url` string and returns a hardcoded mock transcript with generic feedback comments.

---

## 2. Steps to Utilize Real API Keys / AI Models

To replace the mock implementation with real AI grading (e.g., OpenAI Whisper + GPT-4), the following backend steps must be taken:

1. **Environment Configuration:** Set `AI_BACKEND=real` in the backend environment.
2. **Provide API Keys:** Expose necessary credentials (e.g., `OPENAI_API_KEY`) to the Django environment.
3. **Implement the STT & LLM Pipeline:**
   - In `core/ai/backends.py`, the `RealBackend.transcribe_and_score_speaking` method currently raises a `NotImplementedError`.
   - Update this method to:
     - Download the audio file from `submission.audio_recording_url`.
     - Send the audio to a Speech-to-Text service (like OpenAI Whisper) to obtain the transcript.
     - Pass the transcript and the exercise rubric to an LLM to generate the final score and comments.
4. **Admin Configuration:** Create/Configure an active `AiModel` row in the Django Admin panel, defining the `endpoint_url` and `strictness`.
5. **Dependencies:** Ensure the required HTTP clients (`requests`, `httpx`, or the `openai` SDK) are added to `requirements.txt`.

---

## 3. Websocket Status

### Backend: Ready
The backend WebSocket is fully implemented in `SpeakingConsumer` (`core/consumers.py`) at `ws/speaking/<exercise_id>/`. It correctly handles mock `audio_chunk` messages and evaluates the final `audio_recording_url` when the `end` event is fired.

### Frontend: Not Implemented (Optional)
The frontend currently uses a standard one-shot HTTP POST request (`useAiPractice`) to evaluate speaking exercises. The live WebSocket connection (`useSpeakingSession`) is documented as optional in `07-realtime-websockets.md` and has not been built yet. Implementing it would require establishing a WebSocket connection that sends `audio_chunk` messages while recording, followed by an `end` message.

---

## 4. Clarification on Live Conversational AI

Currently, the app **does not contain a live, two-way conversational AI**. 
- It is designed strictly as an automated evaluation tool.
- The user records a monolog (e.g., reading a passage or responding to a prompt).
- The AI's only role is to transcribe the finished audio and grade the pronunciation/fluency. It does not stream audio back or have a real-time back-and-forth conversation with the user.

---

## 5. Current State of the AI Writing Flow

The writing exercise flow mirrors the speaking flow and also currently relies on the **deterministic mock backend**.

### Frontend (`english-learning-web`)
- Users are presented with a prompt and a large `textarea` to type their response.
- The UI tracks the live word count as the user types and allows saving progress locally via a "Save draft" button.
- Submitting the exercise (either as an assignment or for instant AI practice) sends the `writing_text` to the backend.

### Backend (`english-learning-api`)
- The API routes the submission to `core.ai.service.grade_writing`.
- By default, the `MockBackend` is used. It calculates a deterministic score based on the word count (capped at a certain limit) and the presence of specific "good markers" (e.g., *however, therefore, furthermore, in conclusion*).
- It returns a mock score and a generic feedback comment, completely avoiding actual LLM analysis.

### How to enable Real AI for Writing
To utilize a real LLM for writing evaluation:
1. Set `AI_BACKEND=real` in the backend environment.
2. Implement the `RealBackend.grade_writing` method in `core/ai/backends.py` (which currently throws a `NotImplementedError`). This method should combine the `writing_text` and a grading rubric into a prompt, and send it to an LLM API (like GPT-4) to generate a dynamic score and personalized feedback.
