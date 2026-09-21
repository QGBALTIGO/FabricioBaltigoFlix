def test_application_imports():
    import app.main as main

    assert main.app.title
    assert main.app.version == "1.1.0"
