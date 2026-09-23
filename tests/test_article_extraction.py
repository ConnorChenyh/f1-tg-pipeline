import unittest

from analyzer.article_fetcher import _extract_jina_article_body
from generator.evidence_pack import build_topic_grounding


class ArticleExtractionTests(unittest.TestCase):
    def test_scoreboard_before_heading_does_not_displace_article(self):
        noise = '\n\n'.join('Top Events NFL MLB WNBA ' * 8 for _ in range(9))
        paragraphs = [f'Paragraph {i}: ' + 'Relevant racing information. ' * 4 for i in range(12)]
        paragraphs[-1] = 'Ugochukwu finished with two victories and seven podiums on his way to the title.'
        text = 'Markdown Content:\n' + noise + '\n\n# Ugochukwu celebrates title\n\n' + '\n\n'.join(paragraphs)
        result = _extract_jina_article_body(text)
        self.assertNotIn('Top Events', result)
        self.assertIn(paragraphs[-1], result)
        self.assertEqual(len(result.split('\n\n')), 12)

    def test_headingless_article_keeps_short_quote_and_late_details(self):
        quote = '"We knew that was what I needed to win the championship," he said.'
        body = '\n\n'.join(['The driver returned to racing after recovering from an injury.'] * 9 + [quote])
        self.assertIn(quote, _extract_jina_article_body(body))

    def test_navigation_only_is_not_returned_as_article(self):
        self.assertEqual(_extract_jina_article_body('Markdown Content:\n* [Home](https://example.com)\n\n# News\n\nMenu'), '')

    def test_image_scoreboard_is_filtered_without_heading(self):
        noise = 'Final 0 Outs ![Image 4 Team](https://example.com/image) ' * 4
        news = 'The driver scored 159 points and secured the championship in Madrid.'
        self.assertEqual(_extract_jina_article_body(noise + '\n\n' + news), news)

    def test_video_caption_and_footer_do_not_enter_evidence(self):
        news = 'The driver scored 159 points and secured the championship in Madrid.'
        text = '# News\n\nUnrelated recommended racing video highlights (1:40)\n\n' + news
        text += '\n\n*   [Terms of Use](https://example.com/terms)\n\nGAMBLING PROBLEM? Advertising footer text.'
        self.assertEqual(_extract_jina_article_body(text), news)

    def test_grounding_preserves_detail_beyond_old_budget(self):
        article = 'Relevant background. ' * 180 + 'Two wins and seven podiums.'
        topic = {'evidence_posts': [{'url': 'https://example.com/story', 'fetch_status': 'ok', 'article_content': article}]}
        self.assertIn('Two wins and seven podiums.', str(build_topic_grounding(topic)))


if __name__ == '__main__':
    unittest.main()
