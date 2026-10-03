"""Discord 봇: 슬래시 명령(/watch add|list|stop), 감시용 채널 자동 생성과 권한, 채널 삭제·추방 감지."""

import logging
from datetime import datetime, timedelta

import discord
from discord import app_commands
from sqlalchemy import select

from app import service
from app.db import SessionLocal, now_kst
from app.models import Channel, Watch
from app.stations import SEAT_CLASSES, TRAIN_GROUPS, group_types
from app.state import state

log = logging.getLogger(__name__)

CATEGORY = "ktx-감시"
SYSTEM = "시스템"
VIEWER_ONLY = discord.PermissionOverwrite(
    view_channel=True, read_message_history=True, send_messages=False, send_messages_in_threads=False,
    create_public_threads=False, create_private_threads=False, add_reactions=False)
# 비공개 채널 덮어쓰기에 넣는 권한 전부. Discord 는 봇이 서버에서 가진 권한만 덮어쓰기로 허용·거부하게 한다.
# 관리자 권한은 필요 없지만 이 중 하나라도 없으면 채널 생성이 403(Missing Permissions)으로 실패한다
NEEDED = discord.Permissions(
    manage_channels=True, view_channel=True, send_messages=True, read_message_history=True,
    send_messages_in_threads=True, create_public_threads=True, create_private_threads=True, add_reactions=True)


def failed(what: str, e: discord.HTTPException) -> service.Invalid:
    """Discord API 오류를 화면에 보여줄 입력 오류로 바꾼다. 그대로 두면 웹 API 가 500 을 낸다."""
    return service.Invalid(f"Discord 에서 {what} 못했습니다 (HTTP {e.status}). 봇 권한을 확인하거나 잠시 뒤 다시 시도하세요.")


class Bot(discord.Client):
    def __init__(self, members_intent: bool = True) -> None:
        intents = discord.Intents.default()
        intents.members = members_intent  # 멤버 목록(열람자 선택)에 필요한 privileged intent
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.system_channel_id: int | None = None
        self.deleting: set[int] = set()  # 봇이 직접 지우는 채널. 삭제 이벤트를 '사라진 채널'로 보지 않는다
        self.tree.add_command(WatchCommands(), guild=discord.Object(id=state.settings.guild_id))

    @property
    def guild(self) -> discord.Guild | None:
        return self.get_guild(state.settings.guild_id)

    @property
    def ready(self) -> bool:
        return self.is_ready() and not self.is_closed() and self.guild is not None

    async def setup_hook(self) -> None:
        await self.tree.sync(guild=discord.Object(id=state.settings.guild_id))

    async def on_ready(self) -> None:
        if self.guild is None:
            log.error("봇이 DISCORD_GUILD_ID 서버에 들어가 있지 않습니다. 봇 초대 링크로 서버에 추가하세요")
            return
        log.info("Discord 봇 연결됨 guild=%s", self.guild.id)
        if missing := self.missing_permissions():
            log.error("봇 역할에 서버 권한이 부족해 비공개 채널을 만들 수 없습니다: %s", ", ".join(missing))
        else:
            await self.ensure_system_channel()
        await self.resync()

    async def resync(self) -> None:
        g = self.guild
        if g is None:
            return
        me = g.me
        chans = [(c.id, c.name) for c in g.text_channels if c.permissions_for(me).send_messages]
        for cid in service.sync_discord_channels(chans, self.system_channel_id):
            await service.channel_gone(cid, "Unknown Channel")

    async def on_guild_channel_create(self, channel) -> None:
        await self.resync()

    async def on_guild_channel_update(self, before, after) -> None:
        await self.resync()

    async def on_guild_channel_delete(self, channel) -> None:
        if channel.id in self.deleting:
            self.deleting.discard(channel.id)
            return
        if channel.id == self.system_channel_id:
            self.system_channel_id = None
        c = service.find_channel("discord_bot", str(channel.id))
        if c:
            await service.channel_gone(c.id, "Unknown Channel")

    async def on_guild_remove(self, guild: discord.Guild) -> None:
        if guild.id != state.settings.guild_id:
            return
        with SessionLocal() as db:
            ids = [c.id for c in db.scalars(select(Channel).where(Channel.kind == "discord_bot", Channel.broken_at.is_(None)))]
        for cid in ids:
            await service.channel_gone(cid, "Missing Access")

    def missing_permissions(self) -> list[str]:
        have = self.guild.me.guild_permissions  # 관리자면 전부 True
        return [name for name, on in NEEDED if on and not getattr(have, name)]

    def _admins(self) -> dict:
        g = self.guild
        return {g.get_member(a) or discord.Object(id=a, type=discord.Member): discord.PermissionOverwrite(
            view_channel=True, read_message_history=True) for a in state.settings.admin_ids}

    async def ensure_category(self) -> discord.CategoryChannel:
        g = self.guild
        cat = discord.utils.get(g.categories, name=CATEGORY)
        if cat is None:
            cat = await g.create_category(CATEGORY, overwrites={
                g.default_role: discord.PermissionOverwrite(view_channel=False),
                g.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True),
                **self._admins(),
            })
            log.info("카테고리 생성 %s", CATEGORY)
        return cat

    async def ensure_system_channel(self) -> int:
        g = self.guild
        if self.system_channel_id and g.get_channel(self.system_channel_id):
            return self.system_channel_id
        cat = await self.ensure_category()
        ch = discord.utils.get(cat.text_channels, name=SYSTEM)
        if ch is None:
            ch = await g.create_text_channel(SYSTEM, category=cat, overwrites={
                g.default_role: discord.PermissionOverwrite(view_channel=False),
                g.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True),
                **self._admins(),
            })
            log.info("시스템 채널 생성")
        self.system_channel_id = ch.id
        return ch.id

    def _watch_overwrites(self, viewer_ids: list[int]) -> dict:
        """관리자와 고른 멤버만 볼 수 있다. 멤버는 읽기만 된다."""
        g = self.guild
        overwrites = {
            g.default_role: discord.PermissionOverwrite(view_channel=False),
            g.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True,
                                              read_message_history=True),
            **self._admins(),
        }
        for v in viewer_ids:
            if v not in state.settings.admin_ids:
                overwrites[g.get_member(v) or discord.Object(id=v, type=discord.Member)] = VIEWER_ONLY
        return overwrites

    async def create_watch_channel(self, name: str, viewer_ids: list[int]) -> int:
        if missing := self.missing_permissions():
            raise service.Invalid(f"봇 역할에 서버 권한이 부족해 비공개 채널을 만들 수 없습니다: {', '.join(missing)}")
        try:
            cat = await self.ensure_category()
            ch = await self.guild.create_text_channel(name[:90], category=cat, overwrites=self._watch_overwrites(viewer_ids))
        except discord.HTTPException as e:
            raise failed("채널을 만들지", e) from e
        log.info("감시 채널 생성 %s 열람자 %d명", name, len(viewer_ids))
        return ch.id

    async def set_viewers(self, channel_id: int, viewer_ids: list[int]) -> None:
        """이미 만든 채널의 권한 덮어쓰기를 통째로 바꾼다. 채널 권한 수정은 Manage Roles 가 있어야 한다."""
        ch = self.guild.get_channel(channel_id)
        if ch is None:
            raise service.Invalid("Discord 에서 감시 채널을 찾을 수 없습니다.")
        if missing := self.missing_permissions() + ([] if self.guild.me.guild_permissions.manage_roles else ["manage_roles"]):
            raise service.Invalid(f"봇 역할에 서버 권한이 부족해 채널 열람자를 바꿀 수 없습니다: {', '.join(missing)}")
        try:
            await ch.edit(overwrites=self._watch_overwrites(viewer_ids), reason="감시 열람자 변경")
        except discord.HTTPException as e:
            raise failed("채널 권한을 바꾸지", e) from e

    async def delete_channel(self, channel_id: int) -> None:
        ch = self.guild.get_channel(channel_id) if self.guild else None
        if ch is None:
            return
        self.deleting.add(channel_id)
        try:
            await ch.delete(reason="감시 채널 정리")
        except discord.NotFound:
            self.deleting.discard(channel_id)
        except discord.HTTPException as e:
            self.deleting.discard(channel_id)
            raise failed("채널을 지우지", e) from e
        except BaseException:
            self.deleting.discard(channel_id)
            raise

    def list_members(self) -> list[dict]:
        """열람자 후보. 앱 관리자와 서버 관리자(Administrator)는 채널을 항상 볼 수 있어서 뺀다."""
        g = self.guild
        return [{"id": str(m.id), "name": m.display_name, "username": m.name, "avatar": m.display_avatar.with_size(64).url}
                for m in g.members
                if not m.bot and m.id not in state.settings.admin_ids and not m.guild_permissions.administrator]


# ---------- 슬래시 명령 ----------

def is_admin(interaction: discord.Interaction) -> bool:
    return interaction.user.id in state.settings.admin_ids


async def deny(interaction: discord.Interaction) -> None:
    await interaction.response.send_message("관리자만 쓸 수 있는 명령입니다.", ephemeral=True)


async def station_choices(_: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    return [app_commands.Choice(name=s, value=s) for s in state.stations if current in s][:25]


async def arr_choices(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    """출발역에서 갈아타지 않고 갈 수 있는 역만. 시간표에 출발역이 없으면 전체."""
    ok = state.routes.get(getattr(interaction.namespace, "dep", None) or "")
    return [app_commands.Choice(name=s, value=s) for s in state.stations if current in s and (not ok or s in ok)][:25]


async def date_choices(_: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    today = now_kst().date()
    days = [today + timedelta(days=i) for i in range(31)]
    out = []
    for d in days:
        label = f"{d:%Y-%m-%d} ({service.WEEKDAY[d.weekday()]})"
        if current.strip() in label:
            out.append(app_commands.Choice(name=label, value=f"{d:%Y-%m-%d}"))
    return out[:25]


def at_time(date: str, text: str) -> datetime:
    """"07:30", "0730", "730", "7" 모두 받는다. 잘못된 값이면 ValueError."""
    t = text.strip().replace(":", "")
    if not t.isdigit() or len(t) > 4:
        raise ValueError(text)
    h, m = (int(t[:-2]), int(t[-2:])) if len(t) > 2 else (int(t), 0)
    return datetime.strptime(date, "%Y-%m-%d").replace(hour=h, minute=m)


class WatchModal(discord.ui.Modal):
    def __init__(self, dep: str, arr: str, start_date: str, end_date: str) -> None:
        super().__init__(title=f"감시 추가 {dep}→{arr}")
        self.args = (dep, arr, start_date, end_date)
        self.start_time = discord.ui.TextInput(placeholder="00:00 또는 0000", default="00:00", max_length=5)
        self.end_time = discord.ui.TextInput(placeholder="23:59 또는 2359", default="23:59", max_length=5)
        self.basis = discord.ui.RadioGroup(options=[
            discord.RadioGroupOption(label="출발 시각 기준", value="dep", default=True),
            discord.RadioGroupOption(label="도착 시각 기준", value="arr"),
        ])
        self.groups = discord.ui.CheckboxGroup(min_values=1, max_values=len(TRAIN_GROUPS), options=[
            discord.CheckboxGroupOption(label=g["name"], value=g["name"], default=g["name"] == "KTX") for g in TRAIN_GROUPS])
        self.seats = discord.ui.CheckboxGroup(min_values=1, max_values=len(SEAT_CLASSES), options=[
            discord.CheckboxGroupOption(label=s, value=s, default=s == "일반실") for s in SEAT_CLASSES])
        self.add_item(discord.ui.Label(text=f"시작 시각 ({start_date})", component=self.start_time))
        self.add_item(discord.ui.Label(text=f"끝 시각 ({end_date})", component=self.end_time))
        self.add_item(discord.ui.Label(text="기준", component=self.basis))
        self.add_item(discord.ui.Label(text="열차 종류", component=self.groups))
        self.add_item(discord.ui.Label(text="좌석", component=self.seats))

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not is_admin(interaction):
            return await deny(interaction)
        dep, arr, start_date, end_date = self.args
        try:
            start = at_time(start_date, self.start_time.value)
            end = at_time(end_date, self.end_time.value)
        except ValueError:
            return await interaction.response.send_message("시각은 07:00 또는 0700 형식으로 입력하세요.", ephemeral=True)
        await interaction.response.defer(ephemeral=True, thinking=True)
        inp = service.WatchInput(dep=dep, arr=arr, start_at=start, end_at=end, basis=self.basis.value or "dep",
                                 train_types=group_types(self.groups.values), seats=list(self.seats.values),
                                 channel=service.ChannelPick(mode="auto"))
        try:
            w = await service.create_watch(inp, "discord")
        except service.Invalid as e:
            return await interaction.followup.send(str(e), ephemeral=True)
        ch = w.channels[0] if w.channels else None
        await interaction.followup.send(f"감시 #{w.id} 추가했습니다." + (f" 알림 채널: <#{ch.target}>" if ch else ""),
                                        ephemeral=True)


class WatchCommands(app_commands.Group):
    def __init__(self) -> None:
        super().__init__(name="watch", description="취소표 감시")

    @app_commands.command(name="add", description="감시 추가 (시각, 기준, 열차 종류, 좌석은 다음 창에서)")
    @app_commands.rename(dep="출발", arr="도착", start_date="시작일", end_date="끝날짜")
    @app_commands.autocomplete(dep=station_choices, arr=arr_choices, start_date=date_choices, end_date=date_choices)
    async def add(self, interaction: discord.Interaction, dep: str, arr: str, start_date: str, end_date: str | None = None):
        if not is_admin(interaction):
            return await deny(interaction)
        if dep not in state.stations or arr not in state.stations:
            return await interaction.response.send_message("목록에 있는 역을 고르세요.", ephemeral=True)
        try:
            datetime.strptime(start_date, "%Y-%m-%d")
            datetime.strptime(end_date or start_date, "%Y-%m-%d")
        except ValueError:
            return await interaction.response.send_message("날짜는 목록에서 고르세요.", ephemeral=True)
        await interaction.response.send_modal(WatchModal(dep, arr, start_date, end_date or start_date))

    @app_commands.command(name="list", description="진행 중인 감시 목록")
    async def list_(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            return await deny(interaction)
        with SessionLocal() as db:
            rows = db.scalars(select(Watch).where(Watch.status != "ended").order_by(Watch.id)).all()
        status = {"active": "감시중", "paused": "일시정지", "creating": "채널 생성 중"}
        lines = [f"{service.describe(w)} · {status[w.status]} · 조회 {w.scan_count} · 발견 {w.found_count}" for w in rows]
        await interaction.response.send_message("\n".join(lines) or "진행 중인 감시가 없습니다.", ephemeral=True)

    @app_commands.command(name="stop", description="감시 종료")
    @app_commands.rename(watch_id="번호")
    async def stop(self, interaction: discord.Interaction, watch_id: int):
        if not is_admin(interaction):
            return await deny(interaction)
        with SessionLocal() as db:
            w = db.get(Watch, watch_id)
        if not w or w.status == "ended":
            return await interaction.response.send_message(f"진행 중인 감시 #{watch_id} 가 없습니다.", ephemeral=True)
        await interaction.response.defer(ephemeral=True, thinking=True)
        await service.end_watch(watch_id, "manual")
        await interaction.followup.send(f"감시 #{watch_id} 를 종료했습니다.", ephemeral=True)


async def run_bot() -> None:
    """봇을 돌린다. GUILD_MEMBERS intent 가 꺼져 있으면 멤버 목록 없이 다시 연결한다."""
    for members in (True, False):
        bot = Bot(members_intent=members)
        state.bot = bot
        try:
            await bot.start(state.settings.bot_token)
            return
        except discord.PrivilegedIntentsRequired:
            log.error("Developer Portal 에서 SERVER MEMBERS INTENT 를 켜야 열람자 멤버 목록을 불러올 수 있습니다. 멤버 목록 없이 연결합니다")
            await bot.close()
        except discord.LoginFailure:
            log.error("DISCORD_BOT_TOKEN 이 올바르지 않습니다")
            await bot.close()
            return
        except Exception as e:
            log.error("Discord 봇을 시작하지 못했습니다: %s: %s", type(e).__name__, e)
            await bot.close()
            return
