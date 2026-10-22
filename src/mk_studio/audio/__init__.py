"""Audio production and multi-agent review workflow for Mar and Kuku Production Studio.

Coordinates:
Google Music / Lyria Intake -> Manus Timestamped Transcription -> ChatGPT Script Alignment & Trim -> Owner Listening Approval -> Palmier Cues.
"""

from .workflow import (
    AudioIntakeArtifact,
    ChatGPTAudioReview,
    ListeningApprovalTicket,
    ManusTimestampedTranscript,
    TimedDialogueSegment,
    approve_listening_ticket,
    compile_palmier_cues_from_approval,
    create_listening_approval_ticket,
    intake_audio_master,
    review_and_align_with_chatgpt,
    transcribe_with_manus,
)

__all__ = [
    "AudioIntakeArtifact",
    "ChatGPTAudioReview",
    "ListeningApprovalTicket",
    "ManusTimestampedTranscript",
    "TimedDialogueSegment",
    "approve_listening_ticket",
    "compile_palmier_cues_from_approval",
    "create_listening_approval_ticket",
    "intake_audio_master",
    "review_and_align_with_chatgpt",
    "transcribe_with_manus",
]
