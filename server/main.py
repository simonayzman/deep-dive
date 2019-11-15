from os import environ
from flask import Flask, render_template, send_from_directory, request, jsonify
from flask_socketio import SocketIO, emit, join_room
from flask_cors import CORS
from flask_debug import Debug
from datetime import datetime
from uuid import uuid4

import urllib
import json
import google
import firebase_admin
from firebase_admin import credentials
from firebase_admin import firestore
from dotenv import load_dotenv

load_dotenv()

cred_val = environ.get("GOOGLE_CREDENTIALS")
if cred_val == None:
    print("Didn't find environment credentials! Reverting to local file.")
    cred = credentials.Certificate("./keys.json")
else:
    print("Found environment credentials! Converting to json.")
    cred_json = json.loads(cred_val)
    cred = credentials.Certificate(cred_json)
firebase_admin.initialize_app(cred)
db = firestore.client()
users_ref = db.collection("users")
rooms_ref = db.collection("rooms")

configs = {
    "development": {
        "client": {
            "token": "((( DEV ENV )))",
            "api": "http://localhost",
            "port": 8000,
        },
        "server": {"static": "../client/build", "template": "../client/build"},
    },
    "production": {
        "client": {
            "token": "((( PROD ENV )))",
            "api": "https://deep-dive-072193.herokuapp.com",
            # "port": environ.get("PORT"),
        },
        "server": {
            "static": "../client/build/static",
            "template": "../client/build",
        },
    },
}


def getConfig(platform="server"):
    return configs[environ.get("FLASK_ENV")][platform]


app = Flask(
    __name__,
    static_folder=getConfig()["static"],
    template_folder=getConfig()["template"],
)
Debug(app)
CORS(app)
socketio = SocketIO(app, async_mode="eventlet")

# App routes
@app.route("/")
def index():
    config_json_string = json.dumps(getConfig(platform="client"))
    return render_template("index.html", config=config_json_string)


@app.route("/<path:path>")
def indexPath(path):
    return send_from_directory(getConfig()["template"], path)


@app.route("/createRoom")
def createRoomPath():
    room_code = urllib.parse.unquote(request.args.get("roomCode"))
    room_name = urllib.parse.unquote(request.args.get("roomName"))
    initial_data = {
        "meta": {"roomCode": room_code, "roomName": room_name},
        "users": {},
        "matches": {},
    }

    response = None
    try:
        room = rooms_ref.document(room_code).get().to_dict()
        if room is None:
            rooms_ref.document(room_code).set(initial_data)
            response = {"ok": True}
        else:
            response = {
                "error": True,
                "errorType": "Room already exists.",
                "room": room,
            }
    except:
        response = {"error": True, "errorType": "Unknown error"}

    print(f"Room creation for code {room_code} yielded response: {response}")

    return jsonify(response)


@app.route("/checkRoom")
def checkRoomPath():
    room_code = urllib.parse.unquote(request.args.get("roomCode"))
    room = rooms_ref.document(room_code).get().to_dict()

    response = None
    if room is None:
        response = {"error": True}
    else:
        response = room

    print(f"Looking for room {room_code} yielded response: {response}")

    return jsonify(response)


# Socket connections
@socketio.on("connect")
def on_connect():
    print("Socket connected on the back-end.")


@socketio.on("join_room")
def on_join_room(data):
    print(f"Joining room: {data}")
    room_code = data["roomCode"]
    user = data["user"]
    user_id = user["userId"]
    join_room(room_code, user_id)
    rooms_ref.document(room_code).update({f"users.{user_id}": user})
    emit("join_room_success", user)


@socketio.on("update_question_rankings")
def on_update_question_rankings(data):
    print(f"Updating question rankings: {data}\n")
    user_id = data["userId"]
    user_question_rankings = data["userQuestionRankings"]
    room_code = data["roomCode"]

    rooms_ref.document(room_code).update(
        {f"users.{user_id}.userQuestionRankings": user_question_rankings}
    )

    room_data = rooms_ref.document(room_code).get().to_dict()
    users = room_data["users"]
    matches = room_data["matches"]

    for other_user_id in users:
        if other_user_id == user_id:
            continue

        match_id = (
            f"{other_user_id}{user_id}"
            if other_user_id < user_id
            else f"{user_id}{other_user_id}"
        )

        other_user_data = users[other_user_id]
        other_user_question_rankings = other_user_data.get(
            "userQuestionRankings", {}
        )

        common_answered_questions = [
            question_id
            for question_id in user_question_rankings.keys()
            if question_id in other_user_question_rankings.keys()
        ]
        common_liked_questions = []

        match_accumulator = 0.0
        for question_id in common_answered_questions:
            current_user_answer = user_question_rankings[question_id]
            other_user_answer = other_user_question_rankings[question_id]

            if current_user_answer == "dislike":
                if other_user_answer == "dislike":
                    match_accumulator += 1.0
                elif other_user_answer == "indifferent":
                    match_accumulator += 0.5
                elif other_user_answer == "like":
                    match_accumulator += 0.0
                elif other_user_answer == "superlike":
                    match_accumulator += -0.5
            elif current_user_answer == "indifferent":
                if other_user_answer == "dislike":
                    match_accumulator += 0.5
                elif other_user_answer == "indifferent":
                    match_accumulator += 1.0
                elif other_user_answer == "like":
                    match_accumulator += 0.5
                elif other_user_answer == "superlike":
                    match_accumulator += 0.0
            elif current_user_answer == "like":
                if other_user_answer == "dislike":
                    match_accumulator += 0.0
                elif other_user_answer == "indifferent":
                    match_accumulator += 0.5
                elif other_user_answer == "like":
                    common_liked_questions.append(question_id)
                    match_accumulator += 1.0
                elif other_user_answer == "superlike":
                    common_liked_questions.append(question_id)
                    match_accumulator += 1.5
            elif current_user_answer == "superlike":
                if other_user_answer == "dislike":
                    match_accumulator += -0.5
                elif other_user_answer == "indifferent":
                    match_accumulator += 0.0
                elif other_user_answer == "like":
                    common_liked_questions.append(question_id)
                    match_accumulator += 1.5
                elif other_user_answer == "superlike":
                    common_liked_questions.append(question_id)
                    match_accumulator += 2.0

        match_strength = 0
        if common_answered_questions:
            match_strength = match_accumulator / len(common_answered_questions)
        match_data = {
            "matchStrength": match_strength,
            "commonQuestions": common_liked_questions,
        }
        matches[match_id] = match_data
        rooms_ref.document(room_code).update(
            {f"matches.{match_id}": match_data}
        )

    emit("update_matches", {"matches": matches, "users": users}, room=room_code)

