# Antigravity model provider

Плагин подключает авторизованную CLI Antigravity как выбираемый провайдер моделей Hermes. OAuth остаётся во владении `agy`; адаптер передаёт запрос через stdin, преобразует результат в OpenAI-совместимую форму и возвращает Hermes вызовы его инструментов.

## Файлы

| Файл | Назначение и связи | Версия содержимого |
| --- | --- | --- |
| [__init__.py](./__init__.py) | Регистрирует subprocess-провайдер `antigravity`, запускает `agy`, преобразует сообщения, usage и вызовы инструментов между Hermes и Antigravity. | cea1ad7f10d32886eabc0eb75d09a3c2d4ef3d66 |
| [bridge-agent.md](./bridge-agent.md) | Ограничивает дочернюю сессию Agy ролью модельного транспорта и запрещает применение собственных инструментов. | d68a6cb416f49756855a4334c867125fa96e4d0e |
| [plugin.yaml](./plugin.yaml) | Манифест model-provider plugin для обнаружения Hermes. | e5e1d7a6617217741d5f646df8aba64d9e0b948f |
| [test_antigravity_provider.py](./test_antigravity_provider.py) | Проверяет каталог моделей, OpenAI-совместимый ответ, tool bridge и runtime-маршрутизацию через поддельную CLI. | 658246d2c4c56ff939e4f039fcc7076bce700326 |
| [README.md](./README.md) | Карта содержимого и правила работы с папкой. | — (самоописание) |

## Подпапки

| Папка | Назначение |
| --- | --- |
| — | Непосредственных подпапок нет. |

## Работа с папкой

Запуск тестов: `pytest -q plugins/model-providers/antigravity/test_antigravity_provider.py`.

Последняя полная сверка перечня и версий содержимого: 2026-09-21T00:00:00Z.
