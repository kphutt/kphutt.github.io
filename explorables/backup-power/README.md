# The storm test: backup power

An interactive page. Five houses go through the same two-week winter power outage, each with a different
backup-power option:

- a gasoline portable generator
- a battery
- a battery with a gasoline generator
- a natural-gas portable generator
- a natural-gas standby generator

You watch who stays warm and who has to go outside in the storm, then add up a whole outage in the
"What would the whole outage take?" section.

Live at <https://explorables.kphutt.com/backup-power/>.

## Files

- `index.html`: the page's markup. It has no inline script, no style attributes and no event-handler
  attributes. The page fills `data-*` hooks.
- `app.js`: a small view runtime (DOM APIs only, no `innerHTML`, no `eval`) and the simulation and page
  logic.
- `style.css`: all styles, including the self-hosted fonts.
- `assets/fonts/`: Inter and Space Grotesk, both under the SIL Open Font License (license files alongside).

Inline styles are applied through the CSSOM (`element.style`), which `style-src 'self'` allows. Nothing is
loaded from another origin, and the page sends no network requests.

## How it works

One schedule engine plans every chore for each option: setup, refills, oil checks, gas runs, and packing up
before a vacation. The storm, the totals and the ranking all read from it, so they always agree.

- **Sourced:** prices, weights, tank sizes, noise, and refill and oil-check intervals come from
  manufacturer pages, owner's manuals and published installer prices. The fridge rule is USDA's.
- **Illustrative:** about 400 W of household use; 85% of the battery usable; how long each chore takes;
  the gas runs; the weather. "At night" means 8 PM to 7 AM. In "Chores: I tap" mode, a skipped oil check
  runs 6 more hours before the low-oil switch stops it. That figure is illustrative too.

## Sources

- [Honda EU7000iS specifications](https://powerequipment.honda.com/generators/models/eu7000is) and [owner's manual](https://cdn.powerequipment.honda.com/pe/pdf/manuals/00X31Z376230.pdf)
- [Anker SOLIX F3800 Plus](https://us.ankersolix.com/products/f3800-plus) and its [generator input adapter](https://us.ankersolix.com/products/generator-input-adapter)
- [Westinghouse WGen9500TFc](https://westinghouse.com/products/wgen9500tfc-tri-fuel-with-co-sensor) and the [WGen9500 manual](https://apelectric.com/content/PDF/Westpro/wgen9500_owners_manual.pdf)
- [Mad Dog Electric: Seattle standby installed prices by size (2026)](https://callmaddog.com/services/whole-house-generator-installation)
- [NailThePrice: Seattle permit records for standby generators (2023 to 2025)](https://nailtheprice.com/electrical/install-whole-house-generator/bellevue-wa/)
- [LocalGoingRate: Seattle gas line costs (2026)](https://localgoingrate.com/gas-line-cost-seattle)
- [Generac: check standby oil every 24 hours in long outages](https://www.generac.com/resources/emergency-preparedness/how-to-prepare-a-home-standby-generator-before-a-storm/)
- [FoodSafety.gov / USDA: fridge food safe about 4 hours](https://www.foodsafety.gov/food-safety-charts/food-safety-during-power-outage)
- [GeekWire: gas stations without power in the 2024 bomb cyclone](https://www.geekwire.com/2024/seattle-areas-bomb-cyclone-storm-drives-demand-at-ev-charging-stations-and-gas-pumps/)

Prices were checked on November 11, 2026. Information only, not professional advice. Not affiliated with any
company named here.
