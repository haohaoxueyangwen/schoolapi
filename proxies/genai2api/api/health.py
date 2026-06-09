from flask import Blueprint, jsonify

from genai_types import reset_chat_group_id, new_chat_group_id

health_bp = Blueprint('health', __name__)


@health_bp.route('/health', methods=['GET'])
def health_check():
    return jsonify({"status": "ok"}), 200


@health_bp.route('/reset', methods=['POST'])
def reset_dialog():
    """Reset the chatGroupId, starting a fresh dialog on the GenAI web UI.

    Useful when you want to start a new conversation without restarting
    the entire proxy server.
    """
    new_id = reset_chat_group_id()
    return jsonify({"status": "ok", "chat_group_id": new_id}), 200
