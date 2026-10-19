"""
Enhanced Progress Animation System
"""

import time
import math
import random
from typing import Dict, List, Optional, Union

from .constants import (
    SPINNER_FRAMES, PROGRESS_BAR_CHARS, 
    FILE_ICONS, SPEED_EMOJIS, STATUS_EMOJIS
)
from config import ANIMATION_SPEED, ENABLE_WAVE_ANIMATION


class ProgressAnimations:
    """Enhanced animation system with multiple styles"""
    
    SPINNERS = SPINNER_FRAMES
    PROGRESS_BARS = PROGRESS_BAR_CHARS
    FILE_ICONS = FILE_ICONS
    SPEED_EMOJIS = SPEED_EMOJIS
    STATUS_EMOJIS = STATUS_EMOJIS
    
    @staticmethod
    def get_spinner(status: str, index: int) -> str:
        """Get spinner frame for given status"""
        spinners = ProgressAnimations.SPINNERS.get(
            status, 
            ProgressAnimations.SPINNERS["processing"]
        )
        return spinners[index % len(spinners)]
    
    @staticmethod
    def get_progress_bar(
        percentage: float, 
        length: int = 10, 
        style: str = "gradient",
        animated: bool = True
    ) -> str:
        """Get progress bar with animation"""
        chars = ProgressAnimations.PROGRESS_BARS.get(
            style, 
            ProgressAnimations.PROGRESS_BARS["gradient"]
        )
        
        # Clamp percentage between 0 and 100
        percentage = max(0, min(100, percentage))
        
        # Calculate filled positions
        step = 100 / (len(chars) * length)
        filled = percentage / step
        full_chars = int(filled // len(chars))
        partial_char_index = int(filled % len(chars))
        
        # Build bar
        bar = chars[-1] * full_chars
        if partial_char_index > 0:
            bar += chars[partial_char_index]
        
        # Fill remaining with empty
        remaining = length - len(bar)
        if remaining > 0:
            bar += chars[0] * remaining
        
        # Add animation effect for active downloads
        if animated and 0 < percentage < 100:
            pulse_chars = ["░", "▒", "▓", "█", "▓", "▒"]
            pulse_index = int(time.time() * 3 * ANIMATION_SPEED) % len(pulse_chars)
            if len(bar) < length:
                bar = bar[:-1] + pulse_chars[pulse_index]
        
        return bar
    
    @staticmethod
    def get_file_icon(file_type: Optional[str]) -> str:
        """Get appropriate icon for file type"""
        if not file_type:
            return "📁"
        
        file_type_lower = file_type.lower()
        return ProgressAnimations.FILE_ICONS.get(file_type_lower, "📁")
    
    @staticmethod
    def get_speed_emoji(speed_bytes: float) -> str:
        """Get speed indicator emoji"""
        if speed_bytes > 10 * 1024 * 1024:  # >10 MB/s
            return ProgressAnimations.SPEED_EMOJIS["very_fast"]
        elif speed_bytes > 1 * 1024 * 1024:  # >1 MB/s
            return ProgressAnimations.SPEED_EMOJIS["fast"]
        elif speed_bytes > 100 * 1024:  # >100 KB/s
            return ProgressAnimations.SPEED_EMOJIS["medium"]
        elif speed_bytes > 10 * 1024:  # >10 KB/s
            return ProgressAnimations.SPEED_EMOJIS["slow"]
        else:
            return ProgressAnimations.SPEED_EMOJIS["very_slow"]
    
    @staticmethod
    def get_status_emoji(status: str) -> str:
        """Get status indicator emoji"""
        return ProgressAnimations.STATUS_EMOJIS.get(status, "🔄")


class DownloadAnimation:
    """Real-time download animation manager"""
    
    def __init__(self):
        self.frame_index = 0
        self.animation_start = time.time()
        self.wave_offset = random.random() * 100  # Random offset for variety
    
    def get_animation_frame(self, percentage: float, state: str = "downloading") -> str:
        """Get animated frame based on percentage"""
        self.frame_index += 1
        
        # Different animations for different percentages
        if percentage < 25:
            frames = ["▁", "▂", "▃", "▄"]
        elif percentage < 50:
            frames = ["▅", "▆", "▇", "█"]
        elif percentage < 75:
            frames = ["▉", "▊", "▋", "▌"]
        else:
            frames = ["▍", "▎", "▏", "█"]
        
        index = int(self.frame_index * ANIMATION_SPEED) % len(frames)
        return frames[index]
    
    def create_wave_animation(self, percentage: float, width: int = 20) -> str:
        """Create wave-like animation effect"""
        if not ENABLE_WAVE_ANIMATION:
            # Fallback to simple progress bar
            filled = int(width * percentage / 100)
            return "█" * filled + "░" * (width - filled)
        
        wave = ""
        for i in range(width):
            pos = (i / width) * 100
            wave_pos = (time.time() * 2 * ANIMATION_SPEED + i * 0.3 + self.wave_offset) % 1.0
            
            if pos <= percentage:
                # Create wave effect for filled portion
                height = math.sin(wave_pos * math.pi * 2) * 0.3 + 0.7
                if height > 0.8:
                    wave += "█"
                elif height > 0.6:
                    wave += "▓"
                elif height > 0.4:
                    wave += "▒"
                else:
                    wave += "░"
            else:
                wave += "░"
        
        return wave
    
    def get_pulse_effect(self, percentage: float, width: int = 10) -> str:
        """Create pulsing effect for near completion"""
        if percentage < 90:
            return self.create_wave_animation(percentage, width)
        
        # Pulsing effect for final 10%
        pulse = int((math.sin(time.time() * 5) + 1) * 2.5)
        filled = int(width * percentage / 100)
        pulse_char = ["█", "▓", "▒", "░"][pulse % 4]
        
        return "█" * (filled - 1) + pulse_char + "░" * (width - filled)