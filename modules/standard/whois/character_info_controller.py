import time
from functools import partial

from core.aochat.server_packets import BuddyAdded
from core.decorators import instance, command, event, timerevent
from core.db import DB
from core.dict_object import DictObject
from core.text import Text
from core.command_param_types import Character, Const, Int
from core.chat_blob import ChatBlob


@instance()
class CharacterInfoController:
    BUDDY_IS_ONLINE_TYPE = "is_online"

    def __init__(self):
        self.name_history = []
        self.waiting_for_update = {}

    def inject(self, registry):
        self.bot = registry.get_instance("bot")
        self.db: DB = registry.get_instance("db")
        self.text: Text = registry.get_instance("text")
        self.pork_service = registry.get_instance("pork_service")
        self.command_alias_service = registry.get_instance("command_alias_service")
        self.util = registry.get_instance("util")
        self.alts_service = registry.get_instance("alts_service")
        self.alts_controller = registry.get_instance("alts_controller")
        self.buddy_service = registry.get_instance("buddy_service")

    def start(self):
        self.db.exec("CREATE TABLE IF NOT EXISTS name_history (char_id INT NOT NULL, name VARCHAR(20) NOT NULL, created_at INT NOT NULL, PRIMARY KEY (char_id, name))")
        self.command_alias_service.add_alias("w", "whois")
        self.command_alias_service.add_alias("lookup", "whois")
        self.command_alias_service.add_alias("is", "whois")

    @command(command="whois", params=[Character("character"), Int("dimension", is_optional=True), Const("forceupdate", is_optional=True)], access_level="member",
             description="Get whois information for a character")
    def whois_cmd(self, request, char, dimension, force_update):
        dimension = dimension or self.bot.dimension

        if dimension != self.bot.dimension:
            char_info = self.pork_service.request_char_info(char.name, dimension)
            if char_info:
                blob = "Name: %s (%s)\n" % (self.get_full_name(char_info), self.text.make_chatcmd("History", "/tell <myname> history " + char_info.name + str(char_info.dimension)))
                blob += "Profession: %s\n" % char_info.profession
                blob += "Faction: <%s>%s<end>\n" % (char_info.faction.lower(), char_info.faction)
                blob += "Breed: %s\n" % char_info.breed
                blob += "Gender: %s\n" % char_info.gender
                blob += "Level: %d\n" % char_info.level
                blob += "AI Level: <green>%d<end>\n" % char_info.ai_level
                if char_info.org_id:
                    blob += "Org: <highlight>%s<end> (%d) (%s)\n" % (char_info.org_name, char_info.org_id, self.text.make_chatcmd("orglist", "/tell <myname> orglist " + char_info.name))
                    blob += "Org Rank: %s (%d)\n" % (char_info.org_rank_name, char_info.org_rank_id)
                else:
                    blob += "Org: &lt;None&gt;\n"
                    blob += "Org Rank: &lt;None&gt;\n"
                blob += "Head Id: %d\n" % char_info.head_id
                blob += "PVP Rating: %d\n" % char_info.pvp_rating
                blob += "PVP Title: %s\n" % char_info.pvp_title
                blob += "Character Id: %d\n" % char_info.char_id
                blob += "Source: %s\n" % self.format_source(char_info, 0)
                blob += "Dimension: %s\n" % char_info.dimension

                more_info = self.text.paginate_single(ChatBlob("More Info", blob))

                return self.text.format_char_info(char_info) + " " + more_info
            else:
                return "Could not find info for character <highlight>%s<end> on RK%d." % (char.name, dimension)
        elif char.char_id:
            online_status = self.buddy_service.is_online(char.char_id)
            if online_status is None:
                self.bot.add_packet_handler(BuddyAdded.id, self.handle_buddy_status)
                self.waiting_for_update[char.char_id] = DictObject({"char_id": char.char_id,
                                                                    "name": char.name,
                                                                    "callback": partial(self.show_output, char, dimension, force_update, reply=request.reply)})
                self.buddy_service.add_buddy(char.char_id, self.BUDDY_IS_ONLINE_TYPE)
            else:
                self.show_output(char, dimension, force_update, online_status, request.reply)
        else:
            self.show_output(char, dimension, force_update, None, request.reply)


    def show_output(self, char, dimension, force_update, online_status, reply):
        max_cache_age = 0 if force_update else 86400
        char_info = self.pork_service.get_character_info(char.name, max_cache_age)
        if char_info and char_info.source != "chat_server":
            blob = "Name: %s (%s)\n" % (self.get_full_name(char_info), self.text.make_chatcmd("History", "/tell <myname> history " + char_info.name))
            blob += "Character Id: %d\n" % char_info.char_id
            blob += "Profession: %s\n" % char_info.profession
            blob += "Faction: <%s>%s<end>\n" % (char_info.faction.lower(), char_info.faction)
            blob += "Breed: %s\n" % char_info.breed
            blob += "Gender: %s\n" % char_info.gender
            blob += "Level: %d\n" % char_info.level
            blob += "AI Level: <green>%d<end>\n" % char_info.ai_level
            if char_info.org_id:
                blob += "Org: <highlight>%s<end> (%d) (%s)\n" % (char_info.org_name, char_info.org_id, self.text.make_chatcmd("orglist", "/tell <myname> orglist " + char_info.name))
                blob += "Org Rank: %s (%d)\n" % (char_info.org_rank_name, char_info.org_rank_id)
            else:
                blob += "Org: &lt;None&gt;\n"
                blob += "Org Rank: &lt;None&gt;\n"
            #blob += "Head Id: %d\n" % char_info.head_id
            #blob += "PVP Rating: %d\n" % char_info.pvp_rating
            #blob += "PVP Title: %s\n" % char_info.pvp_title
            blob += "Source: %s\n" % self.format_source(char_info, max_cache_age)
            blob += "Dimension: %s\n" % char_info.dimension
            blob += "Status: %s\n" % ("<green>Active<end>" if char.char_id else "<red>Inactive<end>")

            blob += self.get_name_history(char.char_id)

            alts = self.alts_controller.alts_service.get_alts(char.char_id)
            blob += "\n<header2>Alts (%d)<end>\n" % len(alts)
            blob += self.alts_controller.format_alt_list(alts)

            more_info = self.text.paginate_single(ChatBlob("More Info", blob))

            msg = self.text.format_char_info(char_info, online_status) + " " + more_info
        elif char.char_id:
            blob = "<notice>Note: Could not retrieve detailed info for character.<end>\n\n"
            blob += "Name: <highlight>%s<end>\n" % char.name
            blob += "Character ID: <highlight>%d<end>\n" % char.char_id
            if online_status is not None:
                blob += "Online status: %s\n" % ("<green>Online<end>" if online_status else "<red>Offline<end>")
            blob += self.get_name_history(char.char_id)
            msg = ChatBlob("Basic Info for %s" % char.name, blob)
        else:
            msg = "Could not find character <highlight>%s<end> on RK%d." % (char.name, dimension)

        reply(msg)

    def get_name_history(self, char_id):
        blob = "\n<header2>Name History<end>\n"
        data = self.db.query("SELECT name, created_at FROM name_history WHERE char_id = ? ORDER BY created_at DESC", [char_id])
        for row in data:
            blob += "%s [%s]\n" % (row.name, self.util.format_date(row.created_at))
        return blob

    @event(event_type="packet:20", description="Capture name history", is_hidden=True)
    def character_name_event(self, event_type, event_data):
        self.name_history.append(event_data)

    @event(event_type="packet:21", description="Capture name history", is_hidden=True)
    def character_lookup_event(self, event_type, event_data):
        self.name_history.append(event_data)

    @timerevent(budatime="1min", description="Save name history", is_hidden=True)
    def save_name_history_event(self, event_type, event_data):
        with self.db.transaction():
            for entry in self.name_history:
                if self.db.type == DB.SQLITE:
                    sql = "INSERT OR IGNORE INTO name_history (char_id, name, created_at) VALUES (?, ?, ?)"
                else:
                    sql = "INSERT IGNORE INTO name_history (char_id, name, created_at) VALUES (?, ?, ?)"

                self.db.exec(sql, [entry.char_id, entry.name, int(time.time())])

            self.name_history = []

    def get_full_name(self, char_info):
        name = ""
        if char_info.first_name:
            name += char_info.first_name + " "

        name += "\"<highlight>" + char_info.name + "<end>\""

        if char_info.last_name:
            name += " " + char_info.last_name

        return name

    def format_source(self, char_info, max_cache_age):
        if char_info.cache_age == 0:
            return char_info.source
        elif char_info.cache_age < max_cache_age:
            return "%s (cache; %s old)" % (char_info.source, self.util.time_to_readable(char_info.cache_age))
        elif char_info.cache_age > max_cache_age:
            return "%s (old cache; %s old)" % (char_info.source, self.util.time_to_readable(char_info.cache_age))

    def handle_buddy_status(self, packet):
        obj = self.waiting_for_update.get(packet.char_id)
        if obj:
            self.buddy_service.remove_buddy(packet.char_id, self.BUDDY_IS_ONLINE_TYPE)
            del self.waiting_for_update[packet.char_id]
            if not self.waiting_for_update:
                self.bot.remove_packet_handler(BuddyAdded.id, self.handle_buddy_status)

            obj.callback(packet.online == 1)
