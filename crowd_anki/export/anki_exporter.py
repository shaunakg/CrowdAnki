import json
import os

import shutil
from pathlib import Path
from typing import Callable

from github import Github

from .deck_exporter import DeckExporter
from ..anki.adapters.anki_deck import AnkiDeck
from ..representation import deck_initializer
from ..representation.deck import Deck
from ..utils.constants import DECK_FILE_NAME, DECK_FILE_EXTENSION, MEDIA_SUBDIRECTORY_NAME
from ..utils.filesystem.name_sanitizer import sanitize_anki_deck_name
from .note_sorter import NoteSorter
from ..config.config_settings import ConfigSettings
from ..utils.notifier import AnkiModalNotifier, Notifier


class AnkiJsonExporter(DeckExporter):
    def __init__(self, collection,
                 config: ConfigSettings,
                 deck_name_sanitizer: Callable[[str], str] = sanitize_anki_deck_name,
                 deck_file_name: str = DECK_FILE_NAME):
        self.config = config
        self.collection = collection
        self.last_exported_count = 0
        self.deck_name_sanitizer = deck_name_sanitizer
        self.deck_file_name = deck_file_name
        self.note_sorter = NoteSorter(config)

    def export_to_directory(self, deck: AnkiDeck, output_dir=Path("."), copy_media=True, create_deck_subdirectory=True) -> Path:
        deck_directory = output_dir
        if create_deck_subdirectory:
            deck_directory = output_dir.joinpath(self.deck_name_sanitizer(deck.name))
            deck_directory.mkdir(parents=True, exist_ok=True)

        deck = deck_initializer.from_collection(self.collection, deck.name)

        deck.notes = self.note_sorter.sort_notes(deck.notes)

        self.last_exported_count = deck.get_note_count()

        deck_filename = deck_directory.joinpath(self.deck_file_name).with_suffix(DECK_FILE_EXTENSION)
        with deck_filename.open(mode='w', encoding="utf8") as deck_file:
            deck_file.write(json.dumps(deck,
                                       default=Deck.default_json,
                                       sort_keys=True,
                                       indent=4,
                                       ensure_ascii=False))

        self._save_changes(deck)

        if copy_media:
            self._copy_media(deck, deck_directory)

        return deck_directory
    
    def export_to_github(self, deck: AnkiDeck, user, pass, repo, copy_media=True, create_deck_subdirectory=True, notifier=None):
    
        """
        This utility function directly uploads an AnkiDeck to Github in the JSON format.
        
        To authorize it, a username and password must be supplied (note: password should be a Github
        personal access token from https://github.com/settings/tokens - don't try it using your
        actual username and password as that would be highly insecure and against best practices.
        
        Note: if a file already exists on the repo at the location determined, it will be updated.
        """
        
        deck_directory = ""
        if create_deck_subdirectory:
            deck_directory = f"{self.deck_name_sanitizer(deck.name)}/"
        
        filename = deck_directory + self.deck_file_name + DECK_FILE_EXTENSION
        
        deck = deck_initializer.from_collection(self.collection, deck.name)
        deck.notes = self.note_sorter.sort_notes(deck.notes)
        self.last_exported_count = deck.get_note_count()
        
        g = Github(user, pass)
    
        try:
            gh_user = g.get_user()
        except:
            return notifier.warning("Authentication to Github failed", "Authenticating with Github failed. Please check that "
                                                                       "both your username and password are correct. Remember: don't use your "
                                                                       "real Github login password, create a personal access token (https://git.io/token) "
                                                                       "and use that as the password.")
    
        # We find out if the file exists so we can replace it
        # Code snippet from https://stackoverflow.com/a/63445581, CC-BY-SA
           
        try:
            repo = gh_user.get_repo(GITHUB_REPO)
        except:
            return notifier.warning("Unable to find Github repository", "Unable to find your Github repository. Make sure you've created one first: https://repo.new")
        
        all_files = []
        contents = repo.get_contents("")
        
        while contents:
            file_content = contents.pop(0)
            if file_content.type == "dir":
                contents.extend(repo.get_contents(file_content.path))
            else:
                file = file_content
                all_files.append(str(file).replace('ContentFile(path="','').replace('")',''))
                
        try:
            if filename in all_files:
                contents = repo.get_contents(filename)
                new_contents = json.dumps(deck, default=Deck.default_json, sort_keys=True, indent=4, ensure_ascii=False)
                repo.update_file(contents.path, "Automated update from CrowdAnki", new_contents, contents.sha, branch = "main")
            else:
                new_contents = json.dumps(deck, default=Deck.default_json, sort_keys=True, indent=4, ensure_ascii=False)
                repo.create_file(filename, "Automated upload from CrowdAnki", new_contents, branch="main")
        except Exception as e:
            return notifier.warning("Unknown error when uploading file", "Please report this error at https://git.io/JCUKl.\n\n" + str(e))
        
        # Not sure what to return if successful
        return True

    def _save_changes(self, deck, is_export_child=False):
        """Save updates that were made during the export. E.g. UUID fields

        It saves decks, deck configurations and models.

        is_export_child refers to whether this deck is a child for the
        _purposes of the current export operation_.  For instance, if
        we're exporting or snapshotting a specific subdeck, then it's
        considered the "parent" here.  We need the argument to avoid
        duplicately saving deck configs and note models.

        """

        self.collection.decks.save(deck.anki_dict)
        for child_deck in deck.children:
            self._save_changes(child_deck, is_export_child=True)

        if not is_export_child:
            for deck_config in deck.metadata.deck_configs.values():
                self.collection.decks.save(deck_config.anki_dict)

            for model in deck.metadata.models.values():
                self.collection.models.save(model.anki_dict)

        # Notes?

    def _copy_media(self, deck, deck_directory):
        media_directory = deck_directory.joinpath(MEDIA_SUBDIRECTORY_NAME)

        media_directory.mkdir(parents=True, exist_ok=True)

        for file_src in deck.get_media_file_list():
            try:
                shutil.copy(os.path.join(self.collection.media.dir(), file_src),
                            str(media_directory.resolve()))
            except IOError as ioerror:
                print("Failed to copy a file {}. Full error: {}".format(file_src, ioerror))
