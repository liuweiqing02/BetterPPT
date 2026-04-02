from __future__ import annotations

import base64
import tempfile
import unittest
from pathlib import Path

from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE

from app.services.pptx_service import generate_pptx_from_plan


class PPTXServiceTestCase(unittest.TestCase):
    @staticmethod
    def _write_test_png(path: Path) -> None:
        png_bytes = base64.b64decode(
            'iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAYAAABytg0kAAAAFklEQVR42mP8z/CfAQgwgImB'
            'QjAAAwMAA/0Bv0QAAAAASUVORK5CYII='
        )
        path.write_bytes(png_bytes)

    def test_generate_editable_pptx(self) -> None:
        slide_plan = [
            {
                'title': 'Cover',
                'bullets': ['Background', 'Goal'],
                'page_function': 'cover',
                'style_tokens_json': {
                    'accent_color': '#1B6EF3',
                    'background_color': '#F7FAFF',
                    'text_color': '#1E2B39',
                    'font_family': 'Arial',
                },
            },
            {
                'title': 'Comparison',
                'bullets': ['Option A', 'Option B', 'Pros', 'Cons'],
                'page_function': 'comparison',
                'style_tokens_json': {
                    'accent_color': '#0F766E',
                    'background_color': '#F0FDFA',
                    'text_color': '#134E4A',
                    'font_family': 'Arial',
                },
            },
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / 'out.pptx'
            summary = generate_pptx_from_plan(slide_plan=slide_plan, output_path=output_path)

            self.assertTrue(output_path.exists())
            self.assertGreater(output_path.stat().st_size, 0)
            self.assertEqual(summary['page_count'], 2)
            self.assertEqual(len(summary['titles']), 2)

            prs = Presentation(str(output_path))
            self.assertEqual(len(prs.slides), 2)
            self.assertGreater(len(prs.slides[0].shapes), 0)

    def test_generate_pptx_with_tables_and_images(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            image_path = Path(tmpdir) / 'hero.png'
            self._write_test_png(image_path)

            slide_plan = [
                {
                    'title': 'Assets',
                    'bullets': ['Keep text editable', 'Render objects separately'],
                    'page_function': 'content',
                    'style_tokens_json': {
                        'accent_color': '#0F766E',
                        'background_color': '#F0FDFA',
                        'text_color': '#134E4A',
                        'font_family': 'Arial',
                    },
                    'tables': [
                        {
                            'title': 'Key Metrics',
                            'headers': ['Metric', 'Value'],
                            'rows': [
                                ['Users', '128'],
                                ['Conversion', '4.2%'],
                            ],
                        }
                    ],
                    'images': [
                        {
                            'caption': 'Hero image placeholder',
                            'alt_text': 'Hero image placeholder',
                            'image_path': str(image_path),
                            'path': str(image_path),
                        }
                    ],
                }
            ]

            output_path = Path(tmpdir) / 'assets.pptx'
            summary = generate_pptx_from_plan(slide_plan=slide_plan, output_path=output_path)

            self.assertTrue(output_path.exists())
            self.assertGreater(output_path.stat().st_size, 0)
            self.assertEqual(summary['page_count'], 1)
            self.assertEqual(len(summary['titles']), 1)

            prs = Presentation(str(output_path))
            self.assertEqual(len(prs.slides), 1)
            slide = prs.slides[0]
            self.assertTrue(any(getattr(shape, 'has_table', False) for shape in slide.shapes))
            self.assertTrue(any(shape.shape_type == MSO_SHAPE_TYPE.PICTURE for shape in slide.shapes))

            all_text = []
            for shape in slide.shapes:
                if hasattr(shape, 'text') and shape.text:
                    all_text.append(shape.text)
            joined_text = '\n'.join(all_text)
            self.assertIn('Hero image placeholder', joined_text)


if __name__ == '__main__':
    unittest.main()
