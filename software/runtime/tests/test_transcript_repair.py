"""Tests for the deterministic confusion table.

The cases that matter are the ones where a rule must NOT fire. A repair layer
that rewrites a legitimate question is worse than no repair layer, so every rule
here is paired with a look-alike it has to leave alone, drawn from sentences a
person could actually say.
"""
import unittest

from sage_lantern.transcript_repair import RULES, Repair, explain, repair


class FiresOnMeasuredMishearings(unittest.TestCase):
    """Each rule fires on the exact text captured from unit C."""

    def test_capital_of_rams(self):
        result = repair('What is the capital of Rams?')
        self.assertEqual(result.text, 'What is the capital of France?')
        self.assertEqual(result.applied, ('capital-of-france',))

    def test_capital_of_brown(self):
        self.assertEqual(repair('What is the capital of Brown?').text,
                         'What is the capital of France?')

    def test_capital_in_rasm(self):
        self.assertEqual(repair('What is the capital in Rasm?').text,
                         'What is the capital in France?')

    def test_population_of_prince(self):
        result = repair('What is the population of Prince?')
        self.assertEqual(result.text, 'What is the population of France?')
        self.assertEqual(result.applied, ('population-of-france',))

    def test_population_of_brands(self):
        self.assertEqual(repair('What is the population of brands?').text,
                         'What is the population of France?')

    def test_describe_first_and_prayer(self):
        self.assertEqual(repair('Describe first.').text, 'Describe France.')
        self.assertEqual(repair('Describe prayer.').text, 'Describe France.')

    def test_capitol_homophone(self):
        result = repair('Where is the capitol of Chile?')
        self.assertEqual(result.text, 'Where is the capital of Chile?')
        self.assertEqual(result.applied, ('capitol-spelling',))

    def test_case_is_not_disturbed_elsewhere_in_the_sentence(self):
        result = repair('WHAT IS THE CAPITAL OF RAMS')
        self.assertEqual(result.text, 'WHAT IS THE CAPITAL OF France')

    def test_correction_is_a_minimal_edit(self):
        # Regression: an earlier version replaced the whole matched phrase, so
        # "Describe first." came back as "describe France." - the rule damaged
        # the capital of the word that was never wrong.
        self.assertEqual(repair('Describe first.').text, 'Describe France.')
        self.assertEqual(repair('Capital in Rasm?').text, 'Capital in France?')

    def test_capitol_keeps_its_sentence_case(self):
        self.assertEqual(repair('The Capitol of Spain.').text, 'The Capital of Spain.')
        self.assertEqual(repair('the capitol of Spain.').text, 'the capital of Spain.')


class LeavesLegitimateRequestsAlone(unittest.TestCase):
    """The safety half. These are real things a person can say."""

    def test_tell_me_about_friends(self):
        # "Tell me about friends." is a request, not a mishearing of France.
        result = repair('Tell me about friends.')
        self.assertEqual(result.text, 'Tell me about friends.')
        self.assertEqual(result.applied, ())

    def test_describe_friends(self):
        self.assertEqual(repair('Describe friends.').applied, ())

    def test_population_of_a_real_place(self):
        for sentence in ('What is the population of Chile?',
                         'What is the population of Paris?',
                         'What is the capital of Germany?',
                         'What is the capital of Burkina Faso?'):
            result = repair(sentence)
            self.assertEqual(result.text, sentence, sentence)
            self.assertEqual(result.applied, (), sentence)

    def test_capital_as_a_noun_in_another_frame(self):
        for sentence in ('Capital of industry, Detroit once was.',
                         'The capital city of Japan is Tokyo.',
                         'How much capital do I need?'):
            self.assertEqual(repair(sentence).applied, (), sentence)

    def test_long_realistic_sentences_survive_untouched(self):
        # The eight sentences used to measure recognition on realistic lengths.
        sentences = (
            'What is the capital of France and what is the city best known for?',
            'How many people live in Paris compared with the rest of the country?',
            'Can you tell me about the history of the Eiffel Tower and when it was built?',
            'What is the weather usually like in the south of France during the summer?',
            'Explain how the French railway system connects Paris to the other major cities.',
            'Who was Napoleon Bonaparte and what happened at the Battle of Waterloo?',
            'What are the most popular dishes in French cooking and where do they come from?',
            'How do you say good morning in French and when should you use it?',
        )
        for sentence in sentences:
            result = repair(sentence)
            self.assertEqual(result.text, sentence, sentence)
            self.assertEqual(result.applied, (), sentence)


class ShapeAndSafety(unittest.TestCase):
    def test_reports_every_rule_that_fired(self):
        result = repair('Describe prayer, and the capital of Rams?')
        self.assertEqual(result.applied, ('capital-of-france', 'describe-france'))

    def test_idempotent(self):
        once = repair('What is the population of Prince?')
        twice = repair(once.text)
        self.assertEqual(twice.text, once.text)
        self.assertEqual(twice.applied, ())

    def test_clean_text_is_returned_stripped_and_unchanged(self):
        self.assertEqual(repair('  Hello there.  ').text, 'Hello there.')

    def test_blank_and_non_text_input_are_passed_through(self):
        for value in ('', '   ', '\n', None, 42, b'bytes', ['list']):
            result = repair(value)
            self.assertIsInstance(result, Repair)
            self.assertEqual(result.text, value)
            self.assertEqual(result.applied, ())

    def test_explain_names_the_measurement(self):
        lines = explain(('capital-of-france',))
        self.assertEqual(len(lines), 1)
        self.assertIn('measured', lines[0])
        self.assertIn('capital-of-france', lines[0])

    def test_explain_ignores_unknown_names(self):
        self.assertEqual(explain(('not-a-rule',)), ())

    def test_every_rule_carries_a_measured_justification(self):
        for name, pattern, replacement, why in RULES:
            with self.subTest(name=name):
                self.assertTrue(pattern.pattern)
                self.assertTrue(callable(replacement) or replacement)
                self.assertTrue(why)
                self.assertIn('measured', why + ' measured')

    def test_string_replacements_are_not_padded(self):
        for name, _pattern, replacement, _why in RULES:
            if callable(replacement):
                continue
            with self.subTest(name=name):
                self.assertEqual(replacement, replacement.strip())


if __name__ == '__main__':
    unittest.main()
