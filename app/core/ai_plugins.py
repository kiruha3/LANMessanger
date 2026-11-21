PLUGINS = {
    "images": ("Генерация изображений", "Создание картинок, выбор модели и проверка очереди", "Изображения",
               ("generate_image", "list_image_models", "get_image_status", "cancel_image_generation")),
    "chats": ("Другие ИИ-чаты", "Поиск, чтение и переименование чатов, вопросы другому агенту по вашему запросу", "Чаты",
              ("list_chats", "read_chat", "list_agent_models", "ask_agent", "rename_chat")),
}


def disabled_plugins(chat):
    stored = chat.get("disabled_plugins", [])
    return tuple(plugin_id for plugin_id in PLUGINS if isinstance(stored, list) and plugin_id in stored)


def disabled_tools(chat):
    return tuple(tool for plugin_id in disabled_plugins(chat) for tool in PLUGINS[plugin_id][3])
