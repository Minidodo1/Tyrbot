from core.chat_blob import ChatBlob
from core.decorators import instance, command
from core.command_param_types import Any, Const, Time, Options
import time

from core.registry import Registry


class TimerTime(Time):
    def get_regex(self):
        regex = r"(\s+(([0-9]+)([a-z]*))+)"
        return regex + ("?" if self.is_optional else "")

    def process_matches(self, params):
        budatime_str = params.pop(0)
        params.pop(0)
        params.pop(0)
        params.pop(0)

        if budatime_str is None:
            return None
        else:
            budatime_str = budatime_str[1:]
            if budatime_str.isdigit():
                return int(budatime_str) * 60
            else:
                util = Registry.get_instance("util")
                return util.parse_time(budatime_str)


@instance()
class TimerController:
    def __init__(self):
        self.alerts = [60 * 60, 60 * 15, 60 * 1]

    def inject(self, registry):
        self.bot = registry.get_instance("bot")
        self.db = registry.get_instance("db")
        self.util = registry.get_instance("util")
        self.job_scheduler = registry.get_instance("job_scheduler")
        self.command_alias_service = registry.get_instance("command_alias_service")
        self.access_service = registry.get_instance("access_service")

    def start(self):
        self.db.exec("CREATE TABLE IF NOT EXISTS timer (name VARCHAR(255) NOT NULL, char_id INT NOT NULL, channel VARCHAR(10) NOT NULL, "
                     "duration INT NOT NULL, created_at INT NOT NULL, finished_at INT NOT NULL, repeating_every INT NOT NULL, job_id INT NOT NULL)")

        # add scheduled jobs for timers that are already running
        data = self.db.query("SELECT * FROM timer")
        for row in data:
            job_id = self.job_scheduler.scheduled_job(self.timer_alert, row.finished_at, row.name)
            self.db.exec("UPDATE timer SET job_id = ? WHERE name = ?", [job_id, row.name])

        self.command_alias_service.add_alias("timers", "timer")

    @command(command="timer", params=[], access_level="all",
             description="Show current timers")
    def timer_list_cmd(self, request):
        t = int(time.time())
        data = self.db.query("SELECT t.*, p.name AS char_name FROM timer t LEFT JOIN player p ON t.char_id = p.char_id ORDER BY t.finished_at ASC")
        blob = ""
        for timer in data:
            repeats = (" (Repeats every %s)" % self.util.time_to_readable(timer.repeating_every)) if timer.repeating_every > 0 else ""
            blob += "<pagebreak>Name: <highlight>%s<end>\n" % timer.name
            blob += "Time left: <highlight>%s<end>%s\n" % (self.util.time_to_readable(timer.created_at + timer.duration - t, max_levels=None), repeats)
            blob += "Owner: <highlight>%s<end>\n\n" % timer.char_name

        return ChatBlob("Timers (%d)" % len(data), blob)

    @command(command="timer", params=[Const("add", is_optional=True), TimerTime("time"), Any("name", is_optional=True)], access_level="all",
             description="Add a timer")
    def timer_add_cmd(self, request, _, duration, timer_name):
        timer_name = timer_name or self.get_timer_name(request.sender.name)

        if self.get_timer(timer_name):
            return "A timer named <highlight>%s<end> is already running." % timer_name

        t = int(time.time())
        self.add_timer(timer_name, request.sender.char_id, request.channel, t, duration)

        return "Timer <highlight>%s<end> has been set for %s." % (timer_name, self.util.time_to_readable(duration, max_levels=None))

    @command(command="timer", params=[Options(["rem", "remove"]), Any("name")], access_level="all",
             description="Remove a timer")
    def timer_remove_cmd(self, request, _, timer_name):
        timer = self.get_timer(timer_name)
        if not timer:
            return "There is no timer named <highlight>%s<end>." % timer_name

        if self.access_service.has_sufficient_access_level(request.sender.char_id, timer.char_id):
            self.remove_timer(timer_name)
            return "Timer <highlight>%s<end> has been removed." % timer.name
        else:
            return "Error! Insufficient access level to remove timer <highlight>%s<end>." % timer.name

    @command(command="rtimer", params=[Const("add", is_optional=True), TimerTime("start_time"), TimerTime("repeating_time"), Any("name", is_optional=True)], access_level="all",
             description="Add a timer")
    def rtimer_add_cmd(self, request, _, start_time, repeating_time, timer_name):
        timer_name = timer_name or self.get_timer_name(request.sender.name)
        if repeating_time < 60:
            return "The timer named <highlight>%s<end> has not been created, because there is an <highlight>minimum repeating time of 1 minute<end>." % timer_name

        if self.get_timer(timer_name):
            return "A timer named <highlight>%s<end> is already running." % timer_name

        t = int(time.time())
        self.add_timer(timer_name, request.sender.char_id, request.channel, t, start_time, repeating_time)

        return "Repeating timer <highlight>%s<end> will go off in <highlight>%s<end> and repeat every <highlight>%s<end>." % \
               (timer_name, self.util.time_to_readable(start_time), self.util.time_to_readable(repeating_time))

    def get_timer_name(self, base_name):
        # attempt base name first
        name = base_name

        idx = 1
        while self.get_timer(name):
            idx += 1
            name = base_name + str(idx)

        return name

    def get_timer(self, name):
        return self.db.query_single("SELECT * FROM timer WHERE name LIKE ?", [name])

    def add_timer(self, timer_name, char_id, channel, t, duration, repeating_time=0):
        alert_duration = self.get_next_alert(duration)
        job_id = self.job_scheduler.scheduled_job(self.timer_alert, t + alert_duration, timer_name)

        self.db.exec("INSERT INTO timer (name, char_id, channel, duration, created_at, finished_at, repeating_every, job_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                     [timer_name, char_id, channel, duration, t, t + duration, repeating_time, job_id])

    def remove_timer(self, timer_name):
        timer = self.get_timer(timer_name)
        self.job_scheduler.cancel_job(timer.job_id)

        self.db.exec("DELETE FROM timer WHERE name LIKE ?", [timer_name])

    def get_next_alert(self, duration):
        for alert in self.alerts:
            if duration > alert:
                return duration - alert
        return duration

    def timer_alert(self, t, timer_name):
        timer = self.get_timer(timer_name)

        if timer.finished_at > t:
            msg = "Timer <highlight>%s<end> has <highlight>%s<end> left." % (timer.name, self.util.time_to_readable(timer.finished_at - t))

            alert_duration = self.get_next_alert(timer.finished_at - t)
            job_id = self.job_scheduler.scheduled_job(self.timer_alert, t + alert_duration, timer.name)

            self.db.exec("UPDATE timer SET job_id = ? WHERE name = ?", [job_id, timer.name])
        else:
            msg = "Timer <highlight>%s<end> has gone off." % timer.name

            self.remove_timer(timer.name)

            if timer.repeating_every > 0:
                # skip scheduling jobs in the past to prevent backlog of jobs when bot goes offline
                current_t = int(time.time()) - timer.repeating_every
                new_t = t
                while new_t < current_t:
                    new_t += timer.repeating_every
                self.add_timer(timer.name, timer.char_id, timer.channel, new_t, timer.repeating_every, timer.repeating_every)

        if timer.channel == "org":
            self.bot.send_org_message(msg)
        elif timer.channel == "priv":
            self.bot.send_private_channel_message(msg)
        else:
            self.bot.send_private_message(timer.char_id, msg)
