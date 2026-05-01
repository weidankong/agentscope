# -*- coding: utf-8 -*-
"""The weather query tool in agentscope."""

import urllib.error
import urllib.parse
import urllib.request

from ._response import ToolResponse
from ..message import TextBlock


def get_weather(city: str) -> ToolResponse:
    """Get the current weather for a given city using wttr.in.

    Args:
        city (`str`):
            The name of the city to query weather for, e.g. "Beijing",
            "London", "New York".

    Returns:
        `ToolResponse`:
            The response containing the weather information.
    """
    try:
        url = f"https://wttr.in/{urllib.parse.quote(city)}?format=j1"
        req = urllib.request.Request(url, headers={"User-Agent": "curl/7"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = resp.read().decode("utf-8")

        import json
        weather = json.loads(data)

        current = weather["current_condition"][0]
        area = weather["nearest_area"][0]

        result = (
            f"Weather for {area['areaName'][0]['value']}, "
            f"{area['country'][0]['value']}:\n"
            f"  Temperature: {current['temp_C']}°C "
            f"(feels like {current['FeelsLikeC']}°C)\n"
            f"  Condition: {current['weatherDesc'][0]['value']}\n"
            f"  Humidity: {current['humidity']}%\n"
            f"  Wind: {current['windspeedKmph']} km/h "
            f"({current['winddir16Point']})\n"
            f"  Visibility: {current['visibility']} km\n"
            f"  Pressure: {current['pressure']} hPa"
        )

        return ToolResponse(
            content=[TextBlock(type="text", text=result)],
        )

    except urllib.error.HTTPError as e:
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=f"HTTPError: Failed to get weather for '{city}' - "
                    f"{e.code} {e.reason}",
                ),
            ],
        )
    except Exception as e:
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=f"Error: Failed to get weather for '{city}' - {e}",
                ),
            ],
        )
