from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('leakscope_app', '0008_registry'),
    ]

    operations = [
        migrations.AddField(
            model_name='customquery',
            name='provider',
            field=models.CharField(default='shodan', max_length=20),
        ),
        migrations.AddField(
            model_name='search',
            name='provider',
            field=models.CharField(default='shodan', max_length=20),
        ),
    ]
