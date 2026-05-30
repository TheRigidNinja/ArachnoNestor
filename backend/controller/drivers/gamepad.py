"""Gamepad input helpers.

Only this module touches pygame/controller hardware. Callers receive simple
input names such as ``button_0`` or ``dpad_up``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class GamepadSnapshot:
    name: str
    active_inputs: list[str]
    axes: list[float]
    buttons: list[int]
    hats: list[tuple[int, int]]


class GamepadUnavailable(RuntimeError):
    pass


class GamepadReader:
    def __init__(self, index: int = 0):
        os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
        os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
        os.environ.setdefault("SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS", "1")
        try:
            import pygame
        except ImportError as exc:
            raise GamepadUnavailable("pygame is not installed") from exc

        self.pygame = pygame
        pygame.init()
        pygame.joystick.quit()
        pygame.joystick.init()
        count = pygame.joystick.get_count()
        if count <= index:
            raise GamepadUnavailable(f"no controller found (pygame count={count})")
        self.controller = pygame.joystick.Joystick(index)
        self.controller.init()

    @staticmethod
    def devices() -> list[dict]:
        os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
        os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
        os.environ.setdefault("SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS", "1")
        try:
            import pygame
        except ImportError as exc:
            raise GamepadUnavailable("pygame is not installed") from exc

        pygame.init()
        pygame.joystick.quit()
        pygame.joystick.init()
        devices = []
        for index in range(pygame.joystick.get_count()):
            controller = pygame.joystick.Joystick(index)
            controller.init()
            devices.append({
                "index": index,
                "name": controller.get_name(),
                "axes": controller.get_numaxes(),
                "buttons": controller.get_numbuttons(),
                "hats": controller.get_numhats(),
            })
            controller.quit()
        pygame.joystick.quit()
        return devices

    @property
    def name(self) -> str:
        return self.controller.get_name()

    def snapshot(self) -> GamepadSnapshot:
        pygame = self.pygame
        pygame.event.pump()

        axes = [round(self.controller.get_axis(i), 2) for i in range(self.controller.get_numaxes())]
        buttons = [self.controller.get_button(i) for i in range(self.controller.get_numbuttons())]
        hats = [self.controller.get_hat(i) for i in range(self.controller.get_numhats())]

        active = []
        for index, value in enumerate(axes):
            if value > 0.5:
                active.append(f"axis_{index}_pos")
            elif value < -0.5:
                active.append(f"axis_{index}_neg")
        for index, value in enumerate(buttons):
            if value:
                active.append(f"button_{index}")
        for index, (x_value, y_value) in enumerate(hats):
            prefix = "dpad" if index == 0 else f"dpad{index}"
            if y_value > 0:
                active.append(f"{prefix}_up")
            elif y_value < 0:
                active.append(f"{prefix}_down")
            if x_value > 0:
                active.append(f"{prefix}_right")
            elif x_value < 0:
                active.append(f"{prefix}_left")

        return GamepadSnapshot(
            name=self.name,
            active_inputs=active,
            axes=axes,
            buttons=buttons,
            hats=hats,
        )

    def close(self) -> None:
        try:
            self.controller.quit()
        finally:
            self.pygame.quit()
