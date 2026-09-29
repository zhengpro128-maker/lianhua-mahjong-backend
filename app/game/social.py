"""Ephemeral, room-scoped social events, independent of turn/action state."""
import time
import uuid

PHRASES = {'hello', 'hurry', 'nice', 'luck', 'thanks', 'again'}
EMOJIS = {'smile', 'laugh', 'wow', 'cry', 'like', 'gg'}
PROPS = {'tomato', 'coffee', 'hammer'}


class RoomSocial:
    def __init__(self):
        self.last_sent = {}

    def send(self, room, seat, message):
        if type(seat) is not int or not 0 <= seat < 4 or room.seats[seat] is None:
            return False, 'SOCIAL_NOT_SEATED'
        if not room.conn.is_connected(seat) or room.status not in ('playing', 'finished'):
            return False, 'SOCIAL_UNAVAILABLE'
        category, value = message.get('category'), message.get('value')
        if not isinstance(value, str):
            return False, 'SOCIAL_INVALID'
        if category == 'text':
            value = value.strip()
            if not value or len(value) > 60 or any(ord(c) < 32 or ord(c) == 127 for c in value):
                return False, 'SOCIAL_INVALID'
        elif category == 'phrase':
            if value not in PHRASES:
                return False, 'SOCIAL_INVALID'
        elif category == 'emoji':
            if value not in EMOJIS:
                return False, 'SOCIAL_INVALID'
        elif category == 'prop':
            target = message.get('targetSeat')
            if value not in PROPS or type(target) is not int or not 0 <= target < 4 or target == seat:
                return False, 'SOCIAL_INVALID'
            if room.manager is None or target >= len(room.manager.players):
                return False, 'SOCIAL_INVALID'
        else:
            return False, 'SOCIAL_INVALID'
        now = time.monotonic()
        if now - self.last_sent.get(seat, float('-inf')) < 2:
            return False, 'SOCIAL_RATE_LIMIT'
        self.last_sent[seat] = now
        event = {'kind': 'room_social', 'id': uuid.uuid4().hex, 'seat': seat,
                 'category': category, 'value': value}
        if category == 'prop':
            event['targetSeat'] = target
        room.conn.broadcast(event)
        return True, ''
