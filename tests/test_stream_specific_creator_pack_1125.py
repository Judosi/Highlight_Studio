from pathlib import Path

from highlight_studio.api.app import creator_pack
from highlight_studio.core.utils import read_json, write_json
from highlight_studio.services import pipeline
from highlight_studio.services.pipeline import (
    build_stream_context,
    generate_youtube_metadata,
    metadata_specificity_audit,
)


def _project(tmp_path: Path, segments: list[dict]) -> Path:
    write_json(tmp_path / "segments.json", segments)
    write_json(tmp_path / "candidates.json", segments)
    write_json(tmp_path / "transcript.json", [])
    return tmp_path


def test_creator_pack_replaces_old_preset_template_with_game_facts(tmp_path):
    project = _project(
        tmp_path,
        [
            {
                "id": 1,
                "start": 0,
                "end": 42,
                "score": 9.4,
                "title": "Лёва впервые запускает FPV Drone Simulator",
                "reason": "Первый полёт заканчивается аварией",
                "text_preview": "Я впервые управляю FPV-дроном. Дрон врезался в стену.",
            },
            {
                "id": 2,
                "start": 80,
                "end": 130,
                "score": 8.9,
                "title": "Дрон врезается в стену",
                "reason": "Лёва теряет управление и смеётся над аварией",
                "text_preview": "Я потерял управление, он просто упал.",
            },
        ],
    )
    write_json(
        project / "youtube_metadata.json",
        {
            "titles": ["ЭТОТ СБАЛАНСИРОВАННЫЙ / СМЫСЛ ВЫШЕЛ ИЗ-ПОД КОНТРОЛЯ"],
            "description": "Автоматическая нарезка лучших моментов.",
            "tags": ["стрим", "нарезка"],
        },
    )

    pack = creator_pack(
        project,
        {"task_preset_label": "Сбалансированный / смысл", "content_type": "Игровой стрим"},
    )

    joined_titles = " ".join(pack["titles"]).lower()
    assert "сбалансирован" not in joined_titles
    assert "fpv" in joined_titles
    assert "дрон" in pack["description"].lower()
    assert pack["tags"][0].startswith("лёва впервые")
    assert pack["generation_mode"] == "scene_specific_local"
    assert pack["specificity_audit"]["passed"] is True


def test_irl_creator_pack_names_people_place_and_event(tmp_path):
    project = _project(
        tmp_path,
        [
            {
                "id": 1,
                "start": 120,
                "end": 180,
                "score": 9.5,
                "title": "Лёва и Ростик пришли в парикмахерскую",
                "reason": "Они выбирают новую стрижку вместе с мастером",
                "text_preview": "Лёва, какую причёску будем делать? Ростик предлагает побрить его налысо.",
            },
            {
                "id": 2,
                "start": 450,
                "end": 520,
                "score": 9.1,
                "title": "Мастер показывает Лёве новую стрижку",
                "reason": "Ростик удивляется результату преображения",
                "text_preview": "Мастер закончил, Лёва смотрит в зеркало, Ростик смеётся.",
            },
        ],
    )

    data = generate_youtube_metadata(
        project,
        {"content_type": "IRL стрим", "edit_mode": "IRL история", "metadata_ai_enabled": False},
    )

    assert data["ai_used"] is False
    assert any("Лёва" in title and "парикмахер" in title.lower() for title in data["titles"])
    assert "Ростик" in data["description"]
    assert "стриж" in data["description"].lower()
    assert data["specificity_audit"]["passed"] is True
    assert (project / "stream_context.json").exists()
    assert read_json(project / "stream_context.json", {})["stream_kind"] == "IRL"


def test_stream_context_samples_strong_scenes_and_whole_timeline(tmp_path):
    segments = [
        {
            "id": index + 1,
            "start": index * 120,
            "end": index * 120 + 45,
            "score": 10 - index * 0.05,
            "title": f"Событие номер {index + 1} в мастерской",
            "reason": f"Участники обсуждают деталь проекта {index + 1}",
            "text_preview": f"Сцена {index + 1}: собирают устройство и проверяют результат.",
        }
        for index in range(50)
    ]
    project = _project(tmp_path, segments)

    context = build_stream_context(project, {"content_type": "IRL стрим"}, max_items=12)
    starts = [scene["source_start"] for scene in context["scenes"]]

    assert len(starts) == 12
    assert starts[0] == 0
    assert starts[-1] == 49 * 120
    assert context["representative_scene_count"] == 12


def test_specificity_audit_rejects_generic_pack(tmp_path):
    project = _project(
        tmp_path,
        [
            {
                "start": 10,
                "end": 55,
                "score": 9,
                "title": "Дмитрий LIXX неожиданно приходит в гости",
                "reason": "Лёва встречает Дмитрия и начинает разговор о концерте",
                "text_preview": "Дима, привет. Расскажи, как прошёл концерт.",
            }
        ],
    )
    context = build_stream_context(project, {"content_type": "IRL стрим"})
    audit = metadata_specificity_audit(
        {
            "titles": ["Лучшие моменты стрима", "Стрим пошёл не по плану"],
            "description": "Эмоции, реакции, чат и смешные ситуации.",
            "tags": ["стрим", "нарезка", "реакции"],
        },
        context,
    )

    assert audit["passed"] is False
    assert audit["specific_titles"] == 0


def test_ai_metadata_prompt_contains_real_transcript_and_accepts_specific_result(tmp_path, monkeypatch):
    project = _project(
        tmp_path,
        [
            {
                "start": 20,
                "end": 75,
                "score": 9.4,
                "title": "Лёва спорит с таймером в Dark Souls",
                "reason": "Контроллер перестаёт отвечать во время босса",
                "text_preview": "В Dark Souls осталось пять минут. Контроллер отключился прямо перед боссом.",
            },
            {
                "start": 180,
                "end": 240,
                "score": 9.0,
                "title": "Контроллер ломается перед боссом",
                "reason": "Лёва пытается восстановить управление",
                "text_preview": "Верни управление, мне нужно закончить бой с боссом.",
            },
        ],
    )
    prompts: list[str] = []

    class FakeAI:
        def generate_json(self, prompt, **_kwargs):
            prompts.append(prompt)
            return {
                "titles": [
                    "ЛЁВА ПРОТИВ ТАЙМЕРА В DARK SOULS",
                    "КОНТРОЛЛЕР СЛОМАЛСЯ ПЕРЕД БОССОМ",
                ],
                "short_titles": ["Таймер против Лёвы", "Контроллер подвёл на боссе"],
                "description": "Лёва запускает Dark Souls, спорит с таймером и теряет управление перед боссом.",
                "montage_summary": "Стрим про бой с боссом, таймер и сломавшийся контроллер.",
                "short_summary": "Контроллер подвёл Лёву перед боссом.",
                "chapters": ["00:00 Спор с таймером", "00:55 Поломка контроллера"],
                "tags": ["Лёва", "Dark Souls", "таймер", "контроллер", "босс"],
                "hashtags": ["#DarkSouls", "#Лёва"],
                "thumbnail_ideas": ["Лёва, таймер и отключившийся контроллер"],
                "hook_options": ["Начать с поломки контроллера перед боссом"],
            }

    monkeypatch.setattr(pipeline, "make_ai_client", lambda *_args, **_kwargs: FakeAI())
    result = generate_youtube_metadata(
        project,
        {
            "content_type": "Игровой стрим",
            "metadata_ai_enabled": True,
            "metadata_ai_retries": 1,
            "metadata_max_segments": 12,
        },
    )

    assert result["ai_used"] is True
    assert result["specificity_audit"]["passed"] is True
    assert "Контроллер отключился прямо перед боссом" in prompts[0]
    assert "Сбалансированный" in prompts[0]  # Explicitly forbidden, never used as evidence.
    assert result["tags"][0].startswith("лёва спорит")


def test_generic_ai_metadata_is_rejected_and_specific_local_pack_survives(tmp_path, monkeypatch):
    project = _project(
        tmp_path,
        [
            {
                "start": 0,
                "end": 50,
                "score": 9,
                "title": "Дмитрий LIXX неожиданно приходит к Лёве",
                "reason": "Они обсуждают съёмки нового клипа",
                "text_preview": "Дима рассказывает Лёве, как снимали новый клип ночью.",
            },
            {
                "start": 80,
                "end": 135,
                "score": 8.8,
                "title": "Разговор о ночных съёмках клипа",
                "reason": "Дмитрий раскрывает детали со съёмочной площадки",
                "text_preview": "На съёмочной площадке пришлось несколько раз переснимать сцену.",
            },
        ],
    )

    class GenericAI:
        def generate_json(self, *_args, **_kwargs):
            return {
                "titles": ["Лучшие моменты стрима", "Стрим пошёл не по плану"],
                "short_titles": ["Хайлайты", "Реакции"],
                "description": "Эмоции, реакции, разговоры и чат.",
                "montage_summary": "Самые яркие эпизоды.",
                "short_summary": "Лучший момент.",
                "chapters": ["00:00 Начало"],
                "tags": ["стрим", "нарезка"],
                "hashtags": ["#стрим"],
                "thumbnail_ideas": ["Крупная эмоция"],
                "hook_options": ["Начать с сильного момента"],
            }

    monkeypatch.setattr(pipeline, "make_ai_client", lambda *_args, **_kwargs: GenericAI())
    monkeypatch.setattr(pipeline.time, "sleep", lambda *_args: None)
    result = generate_youtube_metadata(
        project,
        {
            "content_type": "IRL стрим",
            "metadata_ai_enabled": True,
            "metadata_ai_retries": 1,
            "metadata_max_segments": 12,
        },
    )

    assert result["ai_used"] is False
    assert "слишком общей" in result["warning"]
    assert any("Дмитрий LIXX" in title for title in result["titles"])
    assert "съём" in result["description"].lower()
