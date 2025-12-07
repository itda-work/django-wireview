from django.utils.log import ServerFormatter as DjangoServerFormatter


class ServerFormatter(DjangoServerFormatter):
    def format(self, record):
        msg = record.msg
        if record.name == "wireview":
            msg = self.style.SUCCESS(msg)
        elif record.name == "django.db.backends":
            msg = self.style.SQL_KEYWORD(msg)
        record.msg = msg
        return super().format(record)
