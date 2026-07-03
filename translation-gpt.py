import json
import os
import time

import openai
import pycountry

from py.utils import load_config

openai.api_key = load_config()["openai"]["openai_key"]

# Initialize a chat conversation with instructions
conversation = {
    "messages": [
        {
            "role": "system",
            "content": """
                You are a helpful assistant tasked with translating English text into various languages. 
                Please ensure you provide consistent translations for recurring terms. 
                When the term "English" comes, please get the translation of "English" in the target Language, not the name of the target language 
                The context is : language files for a travel application.
                Keep emojis where at their original place in the output.
                The output will be filled into the language files so I need only the term in the target language.
            """,
        }
    ]
}


def translate_text(text, source_lang, target_lang):
    if text == "en":
        return source_lang
    try:
        conversation["messages"].append(
            {
                "role": "user",
                "content": f'Translate the following from {source_lang} to {target_lang}: "{text}"',
            }
        )

        response = openai.chat.completions.create(
            model="gpt-4o", messages=conversation["messages"]
        )
        translation = response.choices[0].message.content.strip()

        # Remove enclosing quotes if they exist
        if translation.startswith('"') and translation.endswith('"'):
            translation = translation[1:-1]

        conversation["messages"].append({"role": "assistant", "content": translation})

        print(text, "-", translation)
        return translation
    except Exception as e:
        if "Rate limit reached" in str(e):
            print("Rate limit reached, sleeping for 60 seconds...")
            time.sleep(60)
            return translate_text(
                text, source_lang, target_lang
            )  # Recursive call to try again after sleeping
        else:
            print(f"{text} - Translation error: {e}")
            raise Exception(f"Translation failed: {e}")


def get_flag_emoji(country_code):
    if country_code == "en":
        country_code = "GB"
    return "".join([chr(127397 + ord(char)) for char in country_code.upper()])


def get_language_from_country_code(country_code):
    try:
        language_code = pycountry.languages.get(alpha_2=country_code.lower())
        if language_code:
            return language_code.name
        else:
            return None
    except Exception as e:
        print(f"Error retrieving language for {country_code}: {e}")
        return None


def process_nested_lists(value, target_list, index, source_lang, target_lang):
    if isinstance(value[index], list):
        if len(target_list) <= index:
            target_list.append([])
        for sub_index, sub_item in enumerate(value[index]):
            process_nested_lists(
                value[index], target_list[index], sub_index, source_lang, target_lang
            )
    else:
        if len(target_list) <= index:
            translated_item = translate_text(value[index], source_lang, target_lang)
            target_list.append(translated_item)


# Load the en.json file
with open("lang/en.json", "r", encoding="utf-8") as file:
    en_content = json.load(file)

# List all language files in the lang folder
lang_files = [
    f
    for f in os.listdir("lang")
    if os.path.isfile(os.path.join("lang", f)) and f != "en.json"
]

# Add new languages to en.json after "en"
for lang_file in lang_files:
    language_code = lang_file.split(".")[0]
    if language_code not in en_content:
        language_name = get_language_from_country_code(language_code)
        if language_name:
            flag = get_flag_emoji(language_code)

            # Insert the new language entry right after "en"
            en_keys = list(en_content.keys())
            en_index = en_keys.index("en")
            en_keys.insert(en_index + 1, language_code)
            en_content = {
                key: en_content[key]
                if key != language_code
                else f"{flag} {language_name}"
                for key in en_keys
            }

for lang_file in lang_files:
    language_code = lang_file.split(".")[0]
    print(f"\nProcessing {lang_file}...")

    with open(f"lang/{lang_file}", "r", encoding="utf-8") as file:
        lang_content = (
            json.load(file) if os.path.getsize(f"lang/{lang_file}") > 0 else {}
        )

    for i, (key, value) in enumerate(en_content.items()):
        percent = int((i / len(en_content)) * 100)
        print(f"\r{lang_file} - {percent}%      ", end="", flush=True)
        if isinstance(value, list):
            if key not in lang_content:
                lang_content[key] = []
            for index, _ in enumerate(value):
                process_nested_lists(
                    value, lang_content[key], index, "en", language_code
                )
        elif isinstance(value, dict):
            # Values like plural-form maps ({"one": ..., "other": ...}). Fill only
            # the missing sub-keys so any language-specific extras (e.g. Slavic
            # "few"/"many" added by hand) are preserved across re-runs.
            if not isinstance(lang_content.get(key), dict):
                lang_content[key] = {}
            for sub_key, sub_value in value.items():
                if sub_key not in lang_content[key]:
                    translated = translate_text(sub_value, "en", language_code)
                    if translated:
                        lang_content[key][sub_key] = translated
        else:
            if key not in lang_content:
                translated = translate_text(value, "en", language_code)
                if translated:
                    lang_content[key] = translated
                else:
                    break

    # Re-order the keys based on en_content
    lang_content = {key: lang_content.get(key, "") for key in en_content}

    # Save the updated language file
    with open(f"lang/{lang_file}", "w", encoding="utf-8") as file:
        json.dump(lang_content, file, ensure_ascii=False, indent=4)

# Save back the updated en.json file
with open("lang/en.json", "w", encoding="utf-8") as file:
    json.dump(en_content, file, ensure_ascii=False, indent=4)

print("Processing complete!")
