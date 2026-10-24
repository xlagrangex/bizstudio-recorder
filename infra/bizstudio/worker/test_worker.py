import unittest

from transcribe import drop_hallucinations
from worker import DEFAULT_NAME, auto_name


def cue(start: float, end: float, text: str) -> dict:
    return {"start": start, "end": end, "text": text}


class DefaultNameTest(unittest.TestCase):
    def test_desktop_defaults_are_renamed(self):
        for name in [
            "Bizstudio 2026-10-24 at 00.33.43",
            "Built-in Retina Display (Display) 2026-10-24 12.39 AM",
            "Google Chrome 2026-10-24 9.05 PM",
            "Bizstudio Recording - 2026-10-24",
            "Untitled Video",
        ]:
            self.assertTrue(DEFAULT_NAME.match(name), name)
            self.assertEqual(auto_name(name, {}, "Titolo AI"), "Titolo AI")

    def test_real_titles_are_kept(self):
        for name in ["Demo cliente Rossi", "Riunione del 2026-10-24", "2026-10-24 at 00.33.43"]:
            self.assertFalse(DEFAULT_NAME.match(name), name)
            self.assertIsNone(auto_name(name, {}, "Titolo AI"))

    def test_manual_edit_wins(self):
        self.assertIsNone(auto_name("Bizstudio 2026-10-24 at 00.33.43", {"titleManuallyEdited": True}, "Titolo AI"))


class HallucinationTest(unittest.TestCase):
    def test_noise_tags_are_dropped_or_stripped(self):
        kept = drop_hallucinations([[cue(0, 90, "[rumore di fondo]"), cue(91, 93, "Ciao [risata] a tutti")]])
        self.assertEqual([c["text"] for c in kept], ["Ciao a tutti"])

    def test_stretched_cues_are_dropped(self):
        kept = drop_hallucinations([[cue(0, 90, "Bizstudio"), cue(90, 176, "Hai fatto tutto, tutto, tutto, tutto, tutto."), cue(180, 183, "Cof, cof, cof.")]])
        self.assertEqual([c["text"] for c in kept], ["Cof, cof, cof."])

    def test_repeats_across_consecutive_chunks_are_dropped(self):
        chunks = [
            [cue(0, 2, "Grazie per la visione."), cue(3, 5, "Prima frase.")],
            [cue(90, 92, "grazie per la visione"), cue(93, 95, "Seconda frase.")],
            [cue(180, 182, "Grazie per la visione."), cue(183, 185, "Prima frase.")],
        ]
        self.assertEqual(
            [c["text"] for c in drop_hallucinations(chunks)],
            ["Grazie per la visione.", "Prima frase.", "Seconda frase.", "Prima frase."],
        )

    def test_repeat_within_one_chunk_is_kept(self):
        kept = drop_hallucinations([[cue(0, 1, "Sì."), cue(2, 3, "Sì.")]])
        self.assertEqual(len(kept), 2)


if __name__ == "__main__":
    unittest.main()
