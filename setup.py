import setuptools

setuptools.setup(
    name="appservice_framework",
    version="0.1.0",
    url="https://github.com/cadair/python-appservice-framework",

    author="Stuart Mumford",
    author_email=" ",

    description="A Python 3.5+ appservice framework.",
    long_description=open('README.rst').read(),

    packages=setuptools.find_packages(),

    install_requires=['aiohttp',
                      'click'],

    classifiers=[
        'Development Status :: 2 - Pre-Alpha',
        'Programming Language :: Python',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.5',
        'Programming Language :: Python :: 3.6',
    ],
    entry_points='''
        [console_scripts]
        hangoutsas=appservice_framework.__main__:main
    ''',
)
