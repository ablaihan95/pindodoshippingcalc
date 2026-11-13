# Telegram delivery calculator

The bot asks for the item price in tenge and order weight in kilograms, then recommends **КАРГО** or **ПОЧТА** and shows the total price.

## Calculation

This follows the supplied spreadsheet formulas:

- Postal shipping = item price × 22%.
- Cargo comparison cost = weight × 3.9 × today's National Bank USD rate for weights up to and including 10 kg; above 10 kg it uses 2.9.
- The bot recommends cargo when postal shipping is greater than cargo comparison cost; otherwise it recommends post.
- Postal total = item price + 22%.
- Cargo total = item price + weight × cargo rate × 444.76.

The sheet uses the live USD rate (or 450 if unavailable) to choose the option, but uses the fixed `444.76` in its cargo total. The bot preserves that difference. Set `CARGO_TOTAL_USD_RATE` if you want to change that fixed total-price rate.

## Run

1. Create a Telegram bot with [@BotFather](https://t.me/BotFather) and copy its token.
2. Create and activate a virtual environment, then install dependencies:

   ```sh
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

3. Set the token and run the bot:

   ```sh
   export TELEGRAM_BOT_TOKEN="your-token"
   python bot.py
   ```

The USD rate is fetched from the National Bank RSS endpoint for the current Almaty date. If the request fails or USD is missing, the bot uses `USD_RATE_FALLBACK` (450 by default), matching the spreadsheet fallback.
