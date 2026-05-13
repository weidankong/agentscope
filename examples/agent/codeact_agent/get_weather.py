# -*- coding: utf-8 -*-
"""The get_weather tool in agentscope."""
import requests

from pydantic import BaseModel, Field

from agentscope.tool import ToolResponse
from agentscope.message import TextBlock


class WeatherOutput(BaseModel):
    city: str = Field(description="City name")
    temperature_c: int = Field(description="Temperature in Celsius")
    feels_like_c: int = Field(description="Feels-like temperature in Celsius")
    condition: str = Field(description="Weather condition description")
    humidity: int = Field(description="Humidity percentage")
    wind_speed_kmph: int = Field(description="Wind speed in km/h")
    wind_direction: str = Field(description="Wind direction")


def get_weather(city: str) -> ToolResponse:
    """Get the current weather information for a given city.

    Args:
        city (`str`):
            The name of the city to query weather for, e.g. "Beijing".

    Returns:
        `ToolResponse`:
            The tool response containing the weather information or an error
            message.
    """
    try:
        resp = requests.get(
            "https://wttr.in/" + city,
            params={"format": "j1"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

        current = data["current_condition"][0]

        output = WeatherOutput(
            city=city,
            temperature_c=int(current["temp_C"]),
            feels_like_c=int(current["FeelsLikeC"]),
            condition=current["weatherDesc"][0]["value"],
            humidity=int(current["humidity"]),
            wind_speed_kmph=int(current["windspeedKmph"]),
            wind_direction=current["winddir16Point"],
        )

        text = (
            f"City: {output.city}\n"
            f"Temperature: {output.temperature_c}°C "
            f"(Feels like {output.feels_like_c}°C)\n"
            f"Condition: {output.condition}\n"
            f"Humidity: {output.humidity}%\n"
            f"Wind: {output.wind_speed_kmph} km/h, "
            f"direction {output.wind_direction}"
        )

        return ToolResponse(
            content=[TextBlock(type="text", text=text)],
            metadata=output.model_dump(),
        )
    except requests.RequestException as e:
        return ToolResponse(
            content=[TextBlock(type="text", text=f"Error: {e}")],
        )
    except (KeyError, IndexError) as e:
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=f"Error: Failed to parse weather data: {e}",
                ),
            ],
        )
