from loguru import logger

import tkinter as tk
import ctypes
import csv

try:
    from pygame import mixer  # type: ignore
except ModuleNotFoundError:
    mixer = None
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path

from ctypes import windll, wintypes

windll.shcore.SetProcessDpiAwareness(
    1
)  # Updates all screen and window resolutions by ×1.5. Required for cleaner fonts.
windll.kernel32.SetConsoleTitleW(
    "StopwatchTK"
)  # Changes the title of the console window, if it exists.

DEBUG_MODE = False  #########################################

DEFAULT_ALPHA: float = 0.93
HIDING_ALPHA: float = 0.20

STOPWATCH_BACKGROUND: str = "#181818"
LABEL_PAUSED_COLOUR: str = "grey"
LABEL_RUNNING_COLOUR: str = "white"
LABEL_BREAKING_COLOUR: str = "#2ecc71"  # green
LABEL_STOPPED_COLOUR: str = "#e74c3c"  # red

LONG_BREAK_DIVISOR: float = 4
SHORT_BREAK_DIVISOR: float = 5
BREAK_CUTOFF_SECONDS: float = (
    5 * 60
)  # You need to have studied at least that many seconds to start a break.

MESSAGES: list[str] = [
    "Bitte trinkt wasser",
    "Go outside, eat an apple, and touch grass or something",
]

BASE_DIR = Path(__file__).parent
PROGRESS_CSV_PATH = BASE_DIR / "data" / "progress.csv"
BREAK_OVER_SOUND_PATH = BASE_DIR / "assets" / "wine-glass-alarm.ogg"
ERROR_SOUND_PATH = BASE_DIR / "assets" / "windows-xp-error.mp3"
STOPWATCH_FONT_PATH = BASE_DIR / "assets" / "Seven Segment.ttf"
LOG_PATH = BASE_DIR / "log.log"

logger.add(LOG_PATH)

WIDTH = 150
HEIGHT = 0
SNAP_DISTANCE: int = 30  # How close (in pixels) a window edge must be to a work-area edge before it snaps to it.
SPI_GETWORKAREA: int = 0x0030  # SystemParametersInfoW action for the primary monitor's usable area (screen minus taskbar).
STOPWATCH_FONT: tuple[str, int, str] = ("Consolas", 30, "normal")


ERROR_STRING: str = "ẽ̸̛̝̘͈͔͓͇̓͗̒̀͐̄̒̄̏̄͘͜͜͝͝͠͝ͅR̶͉͙̹̩̘̳̯̜̘͉̯̠̾̑̐́̊̂͗͑͐͑̅̕̕R̴͕͍̓0̸̢̡̭͚̟̫̓̆̊͠R̸̤̗̘̻͒̃̈̃̓̊̐̀̎̊͋̚"


class WTFError(Exception):
    def __init__(self, *args, **kwargs):
        logger.info(repr(args))
        logger.info(repr(kwargs))
        play_sound(ERROR_SOUND_PATH)


class State(Enum):
    PAUSED = 0  # Default. Starts here only. No need for an UNINITIALISED state.
    RUNNING = 1
    BREAKING = 3
    STOPPED = 4  # break timer goes to 0


class Stopwatch(tk.Tk):
    # __slots__ = 'state', 'hiding', 'start_time', 'label', 'x', 'y'

    def __init__(self, *args, **kwargs) -> None:
        logger.info("Stopwatch initialised.")

        tk.Tk.__init__(self, *args, **kwargs)
        self.overrideredirect(True)  # Rips out the titlebar
        self.attributes("-topmost", True)

        self.config(bg=STOPWATCH_BACKGROUND)
        self.alpha: float = DEFAULT_ALPHA
        self.attributes("-alpha", self.alpha)
        self.minsize(width=WIDTH, height=HEIGHT)
        self.geometry("+0+800")

        self.label: tk.Label = tk.Label(
            self,
            text="00",
            foreground=LABEL_PAUSED_COLOUR,
            font=STOPWATCH_FONT,
            bg=STOPWATCH_BACKGROUND,
        )
        self.label.pack()

        self.change_state(State.PAUSED)
        self.hiding: bool = False
        self.start_time: datetime = datetime.now()
        self.running_time: timedelta = timedelta()
        self.remaining_break_time: timedelta = timedelta()
        self.bind_everything()
        if DEBUG_MODE:
            self.start_stop()

    def bind_everything(self) -> None:
        def Keypress(event):
            if event.char == " ":
                self.start_stop()
            elif event.char == "h":
                self.hide()

        self.bind("<Key>", Keypress)
        self.bind("<Shift_L><Shift_R>", (lambda event: self.start_stop_break()))
        self.bind("<Right>", (lambda event: self.fast_forward()))
        self.bind("<Left>", (lambda event: self.rewind()))
        self.bind("<Control_L><Left>", (lambda event: self.reset()))
        self.bind("<Escape>", (lambda event: self.kill()))

        self.x = 0
        self.y = 0
        self.bind("<Button-1>", self.click)
        self.bind("<B1-Motion>", self.drag)
        self.bind("<Enter>", self.hover)
        self.bind("<Leave>", self.mouse_leave)

    def click(self, event) -> None:
        self.x = event.x
        self.y = event.y
        self.attributes("-alpha", self.alpha - 0.15)

    def drag(self, event) -> None:
        x = event.x - self.x + self.winfo_x()
        y = event.y - self.y + self.winfo_y()
        x, y = self.snap_coords(x, y)
        self.geometry(f"+{x}+{y}")
        self.attributes("-alpha", self.alpha - 0.15)

    def mouse_leave(self, event) -> None:
        self.attributes("-alpha", self.alpha)

    def hover(self, event) -> None:
        self.attributes("-alpha", self.alpha - 0.15)

    @staticmethod
    def work_area() -> tuple[int, int, int, int]:
        """Return (left, top, right, bottom) of the primary monitor's usable area.

        This excludes the taskbar, so 'snap to bottom' lands on top of it
        rather than underneath it.
        """
        rect = wintypes.RECT()
        if windll.user32.SystemParametersInfoW(
            SPI_GETWORKAREA, 0, ctypes.byref(rect), 0
        ):
            return rect.left, rect.top, rect.right, rect.bottom
        return (
            0,
            0,
            windll.user32.GetSystemMetrics(0),
            windll.user32.GetSystemMetrics(1),
        )

    def snap_coords(self, x: int, y: int) -> tuple[int, int]:
        """Snap each axis to a work-area border edge if it is close enough.

        Axes are snapped independently, so running into a single edge sticks
        to that whole edge, while hitting two edges at once gives a corner.
        """
        w: int = self.winfo_width()
        h: int = self.winfo_height()
        left, top, right, bottom = self.work_area()

        if abs(x - left) <= SNAP_DISTANCE:
            x = left
        elif abs((x + w) - right) <= SNAP_DISTANCE:
            x = right - w

        if abs(y - top) <= SNAP_DISTANCE:
            y = top
        elif abs((y + h) - bottom) <= SNAP_DISTANCE:
            y = bottom - h

        return x, y

    def hide(self) -> None:
        if self.hiding:
            self.alpha = DEFAULT_ALPHA
        else:
            self.alpha = HIDING_ALPHA
        self.attributes("-alpha", self.alpha)
        self.hiding = not self.hiding

    def change_state(self, state: State) -> None:
        """The single place where `self.state` is mutated.

        Keeping the state and its label colour in one transition avoids the
        two ever drifting out of sync.
        """
        self.state = state
        match state:
            case State.PAUSED:
                self.label.config(fg=LABEL_PAUSED_COLOUR)
            case State.RUNNING:
                self.label.config(fg=LABEL_RUNNING_COLOUR)
            case State.BREAKING:
                self.label.config(fg=LABEL_BREAKING_COLOUR)
            case State.STOPPED:
                self.label.config(fg=LABEL_STOPPED_COLOUR)
            case _:
                raise WTFError(f"Invalid state: {self.state}")

    def start_stop(self) -> None:
        match self.state:
            case State.PAUSED:
                self.change_state(State.RUNNING)
                self.start_time = datetime.now() - self.running_time
                self.run()
            case State.STOPPED:
                self.change_state(State.RUNNING)
                self.start_time = datetime.now()
                self.run()
            case State.RUNNING:
                self.change_state(State.PAUSED)
                self.update_display()
            case State.BREAKING:
                # Not allowed to pause in a breaking state. Feature, not a bug!
                play_sound(ERROR_SOUND_PATH)

    def start_stop_break(self) -> None:
        match self.state:
            case State.BREAKING:  # Cancel break and start studying again
                self.change_state(State.RUNNING)
                self.update_display()
            case State.STOPPED:
                play_sound(ERROR_SOUND_PATH)
            case _:
                if self.running_time.total_seconds() < BREAK_CUTOFF_SECONDS:
                    play_sound(ERROR_SOUND_PATH)
                    return

                if self.running_time.total_seconds() >= 50 * 60:
                    self.remaining_break_time = self.running_time / LONG_BREAK_DIVISOR
                else:
                    self.remaining_break_time = self.running_time / SHORT_BREAK_DIVISOR

                self.start_time = datetime.now()
                was_paused = self.state == State.PAUSED
                self.change_state(State.BREAKING)
                if was_paused:
                    self.run()
                self.log(self.remaining_break_time)
                self.message()

    def run(self) -> None:
        if self.state == State.RUNNING:
            self.update_display()
        elif self.state == State.BREAKING:
            self.update_break_display()
        # TODO: Maybe use a match statement instead, and automatically start running if state is PAUSED; should eliminate boilerplate?
        self.after(100, self.run)

    def update_display(self) -> None:
        self.running_time = datetime.now() - self.start_time
        self.label.config(text=self.format_count(self.running_time))

    def update_break_display(self) -> None:
        t: timedelta = datetime.now() - self.start_time
        if t >= self.remaining_break_time:
            self.change_state(State.STOPPED)
            self.label.config(text=self.format_count(timedelta()))
            play_sound(BREAK_OVER_SOUND_PATH)
            self.remaining_break_time = timedelta()
            return
        self.label.config(
            text=self.format_count(self.remaining_break_time - t, show_seconds=True)
        )

    @staticmethod
    def format_count(t: timedelta, show_seconds: bool = False) -> str:
        if t.days < 0:
            return ERROR_STRING  # fnuny

        if DEBUG_MODE:
            return str(int(t.total_seconds() // 1))
        if show_seconds:
            return f"{int(t.total_seconds() // 1):02d}"
        minutes = int(t.total_seconds() // 60)
        if minutes <= 9:
            return f"0{minutes}"
        return str(minutes)

    @staticmethod
    def log(t: timedelta) -> None:
        if DEBUG_MODE:
            logger.info(f"Break with {t.total_seconds()} seconds initialised.")
        with open(PROGRESS_CSV_PATH, "w") as csvf:
            writer = csv.writer(csvf, delimiter=",")
            writer.writerow((datetime.now().isoformat(), f"{t.total_seconds()}"))

    def message(
        self,
    ) -> None:  # TODO: If this function turns out to be small enough, just fit it in start_stop_break
        pass
        # Message(self).mainloop()

    def fast_forward(self) -> None:
        match self.state:
            case State.RUNNING | State.PAUSED:
                self.start_time -= timedelta(seconds=10)
                self.update_display()
            case State.BREAKING:
                self.start_time += timedelta(seconds=10)

    def rewind(self) -> None:
        match self.state:
            case State.RUNNING | State.PAUSED:
                if self.running_time.total_seconds() < 10:
                    self.start_time += self.running_time
                else:
                    self.start_time += timedelta(seconds=10)
                self.update_display()
            case State.BREAKING:
                self.start_time -= timedelta(seconds=10)

    def reset(self) -> None:
        self.start_time = datetime.now()
        self.update_display()

    def kill(self) -> None:
        if self.state == State.BREAKING:
            t: timedelta = self.remaining_break_time
        else:
            t: timedelta = self.running_time
        logger.info(
            f"Stopwatch killed using `Esc` with {t} seconds on the clock. Best of luck with everything."
        )
        self.destroy()


"""
class Message(tk.Toplevel):
    # The message should be small and somewhere on the lower right corner of the screen.
    # Use MESSAGES = [...] which is defined globally.

    def __init__(self, master, *args, **kwargs) -> None:
        tk.Toplevel.__init__(master, *args, **kwargs)
"""


def play_sound(sound_path) -> None:
    if mixer is None:
        logger.error("pygame.mixer is not available.")
        return
    try:
        mixer.init()
        mixer.music.load(sound_path)
        mixer.music.set_volume(1)
        mixer.music.play()
    except Exception as message:
        logger.error(message)


def main() -> None:
    Stopwatch().mainloop()


if __name__ == "__main__":
    main()

