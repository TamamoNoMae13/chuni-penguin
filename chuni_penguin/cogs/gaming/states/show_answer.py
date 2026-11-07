import asyncio
import contextlib
import io
from typing import TYPE_CHECKING, override

import discord
import rapidfuzz
from discord.utils import escape_markdown
from rapidfuzz import fuzz

from chuni_penguin.cogs.botutils import CachedAlias
from chuni_penguin.database import Character, Song

from .base import GuessingGameState
from .end_game import (
    EndGameReachedQuestionLimit,
    EndGameReachedScoreLimit,
    EndGameTimedOut,
    EndGameTooManyWrongAnswers,
    EndGameUserCanceled,
    EndGameVoiceDisconnected,
)
from .wait import WaitState

if TYPE_CHECKING:
    from chuni_penguin.cogs.gaming._session import GuessingGameSession


async def _mark_correct(answers: list[tuple[discord.Message, float]]):
    # Reactions are only added once the question is over, so they can't leak the
    # answer to other players.
    async def react(message: discord.Message):
        with contextlib.suppress(discord.HTTPException):
            await message.add_reaction("✅")

    await asyncio.gather(*[react(message) for message, _ in answers])


def _format_others(answers: list[tuple[discord.Message, float]]) -> str:
    """e.g. " Also correct: @B (2.10s), @C (3.41s)" for everyone after the first."""
    if len(answers) <= 1:
        return ""

    others = ", ".join(
        f"{message.author.mention} ({seconds:.2f}s)" for message, seconds in answers[1:]
    )
    return f"\nAlso correct: {others}"


class ShowAnswerState(GuessingGameState):
    __slots__ = (
        "aliases",
        "answer_image",
        "answers",
        "session",
        "skipped",
        "song",
        "timed_out",
    )

    def __init__(
        self,
        session: "GuessingGameSession",
        song: Song,
        aliases: list[CachedAlias],
        answer_image: io.BufferedIOBase,
        answers: list[tuple[discord.Message, float]],
        *,
        timed_out: bool = False,
        skipped: bool = False,
    ) -> None:
        self.session = session
        self.song = song
        self.aliases = aliases
        self.answer_image = answer_image
        self.answers = answers
        self.timed_out = timed_out
        self.skipped = skipped

    @override
    async def __call__(self) -> "GuessingGameState | None":
        from chuni_penguin.cogs.gaming._session import GuessingGameType

        guess_time: float | None = None

        if len(self.answers) > 0:
            first_answer, guess_time = self.answers[0]
            accepted_user = first_answer.author

            await _mark_correct(self.answers)
            await self.session.award_points(self.answers)

            content_lower = first_answer.content.lower()
            (_, accuracy, _) = rapidfuzz.process.extractOne(
                content_lower,
                [alias.alias for alias in self.aliases],
                scorer=fuzz.QRatio,
            )

            content = (
                f"{accepted_user.mention} has the correct answer ({accuracy:.2f}%)!"
                + _format_others(self.answers)
            )
            color = discord.Color.green()
        elif self.timed_out:
            content = "Time's up!"
            color = discord.Color.red()
        elif self.skipped:
            content = "Skipped!"
            color = discord.Color.red()
        else:
            content = "Unknown reason."
            color = discord.Color.red()

        description_parts = [f"**Answer**: {escape_markdown(self.song.title)}\n"]

        if len(self.song.aliases) > 0:
            description_parts.append(
                f"-# {' / '.join([escape_markdown(x.alias) for x in self.song.aliases])}\n"
            )

        description_parts.append("\n")
        description_parts.append(f"**Artist**: {escape_markdown(self.song.artist)}\n")
        description_parts.append(f"**Category**: {escape_markdown(self.song.genre)}")

        embed = discord.Embed(
            color=color,
            description="".join(description_parts),
        )
        embed.set_image(url="attachment://image.webp")

        if guess_time is not None:
            embed.set_footer(text=f"Guessed in {guess_time:.2f} seconds")

        if self.session.stopped_by:
            next_state = EndGameUserCanceled(self.session)
        elif (
            self.session.game_type == GuessingGameType.VOICE_CHANNEL
            and self.session.voice_client is None
        ):
            next_state = EndGameVoiceDisconnected(self.session)
        elif self.session.check_score_limit_reached():
            next_state = EndGameReachedScoreLimit(self.session)
        elif self.session.check_question_limit_reached():
            next_state = EndGameReachedQuestionLimit(self.session)
        elif self.session.check_wrong_answers_limit_reached():
            next_state = EndGameTooManyWrongAnswers(self.session)
        elif self.session.questions_timed_out >= 3:
            next_state = EndGameTimedOut(self.session, 3)
        else:
            content += " Next question in 3 seconds..."
            next_state = WaitState(
                self.session, 3, self.session.question_state_cls(self.session)
            )

        await self.session.channel.send(
            content=content,
            embed=embed,
            file=discord.File(self.answer_image, "image.webp"),
        )
        await asyncio.to_thread(self.answer_image.close)

        return next_state


class ShowCharacterAgeAnswerState(GuessingGameState):
    __slots__ = (
        "answers",
        "character",
        "session",
        "skipped",
        "timed_out",
    )

    def __init__(
        self,
        session: "GuessingGameSession",
        character: Character,
        answers: list[tuple[discord.Message, float]],
        *,
        timed_out: bool = False,
        skipped: bool = False,
    ) -> None:
        self.session = session
        self.character = character
        self.answers = answers
        self.timed_out = timed_out
        self.skipped = skipped

    @override
    async def __call__(self) -> "GuessingGameState | None":
        guess_time: float | None = None

        if len(self.answers) > 0:
            first_answer, guess_time = self.answers[0]
            accepted_user = first_answer.author

            await _mark_correct(self.answers)
            await self.session.award_points(self.answers)

            content = f"{accepted_user.mention} has the correct answer!" + _format_others(
                self.answers
            )
            color = discord.Color.green()
        elif self.timed_out:
            content = "Time's up!"
            color = discord.Color.red()
        elif self.skipped:
            content = "Skipped!"
            color = discord.Color.red()
        else:
            content = "Unknown reason."
            color = discord.Color.red()

        embed = discord.Embed(
            title=self.character.name,
            color=color,
            description=(
                f"**Age**: {escape_markdown(self.character.age_text)}\n"
                f"**Accepted answers**: {', '.join([str(a) for a in self.character.ages])}"  # pyright: ignore[reportOptionalIterable]
            ),
        )
        # embed.set_image(url="attachment://image.webp")

        if guess_time is not None:
            embed.set_footer(text=f"Guessed in {guess_time:.2f} seconds")

        if self.session.stopped_by:
            next_state = EndGameUserCanceled(self.session)
        elif self.session.check_score_limit_reached():
            next_state = EndGameReachedScoreLimit(self.session)
        elif self.session.check_question_limit_reached():
            next_state = EndGameReachedQuestionLimit(self.session)
        elif self.session.check_wrong_answers_limit_reached():
            next_state = EndGameTooManyWrongAnswers(self.session)
        elif self.session.questions_timed_out >= 3:
            next_state = EndGameTimedOut(self.session, 3)
        else:
            content += " Next question in 3 seconds..."
            next_state = WaitState(
                self.session, 3, self.session.question_state_cls(self.session)
            )

        await self.session.channel.send(content=content, embed=embed)

        return next_state
