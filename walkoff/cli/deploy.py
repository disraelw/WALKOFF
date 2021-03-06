import click


@click.command()
@click.pass_context
@click.option('-p', '--path', help='Path to compressed app')
def app(ctx, path):
    """
    WIP - Deploys a new app to WALKOFF
    """
    click.echo('Deploy an app by using unzipping and using kubectl to copy to the mounted volume')
    pass


@click.command()
@click.pass_context
@click.option('-p', '--path', help='Path to compressed app')
def interface(ctx, path):
    """
    WIP - Deploys a new interface to WALKOFF
    """
    click.echo('Deploy an interface by using unzipping and using kubectl to copy to the mounted volume')
    pass


commands = [app, interface]
