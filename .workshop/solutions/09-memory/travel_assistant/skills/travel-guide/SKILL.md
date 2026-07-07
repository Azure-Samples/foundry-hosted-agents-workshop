---
name: travel-guide
description: Creates a colorful, downloadable PDF travel guide that bundles a day-by-day itinerary, local highlights, and practical tips for a destination, grounded in the destinations index. Use when the traveler wants a shareable trip guide, a day-by-day plan, or a printable trip outline.
---

# Travel guide skill

Use this skill when the traveler wants a downloadable, shareable trip guide or a
day-by-day itinerary. It renders a multi-page PDF that combines a cover, grounded
local notes, a day-by-day itinerary, and practical tips.

## Workflow

1. Identify the destination city, trip length, interests, and tone/audience.
2. Prefer retrieved destination knowledge from the destinations index. Summarize the
   most relevant retrieved facts and pass them as `source_summary` so the right city
   context is rendered into the guide.
3. If the user did not specify a trip length, use `3` days (maximum `7`).
4. Run the travel-guide script:
   - skill name: `travel-guide`
   - script name: `scripts/create_travel_guide.py`
   - args: a JSON array of string CLI flags and their values. Supported flags:
     - `--city <city>`: destination city, required
     - `--days <number>`: number of itinerary days (1-7), optional, defaults to `3`
     - `--interests <list>`: comma-separated interests, optional
     - `--tone <style>`: guide style or audience, optional
     - `--source-summary <text>`: concise summary of retrieved destination facts to ground the guide, optional
5. After the script returns JSON, tell the traveler the guide was created, share the
   `path`, and summarize what is inside.

## Output contract

The script returns JSON with `city`, `days`, `interests`, `pages`, `path`, `grounded`,
and `message`.

## Available scripts

`scripts/create_travel_guide.py` - Generates a colorful PDF travel guide and returns a JSON with the saved file path.

## Attribution

`scripts/create_travel_guide.py` is adapted from the Microsoft Foundry samples
`07-skills` travel-guide sample and is used under the MIT License
(Copyright (c) 2025 Microsoft Corporation). See the license header in the script.

## Example script arguments

```json
["--city", "Lisbon", "--days", "4", "--interests", "food,viewpoints,history,neighborhoods", "--tone", "first-time visitors who like walking", "--source-summary", "The index highlights Alfama, Belem, miradouros, seafood, and day trips to Sintra."]
```
