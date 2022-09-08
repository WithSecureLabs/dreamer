import setuptools

with open('README.md', 'r') as f:
    long_description = f.read()

setuptools.setup(
    name='dreamer',
    version='1.0.0',
    author='Anssi Matti Helin',
    author_email='amhelin@iki.fi',
    description='Easier cloud infrastructure with Terraform and Ansible',
    long_description=long_description,
    long_description_type='text/markdown',
    url='https://github.com/WithSecureLabs/dreamer',
    packages=setuptools.find_packages(),
    entry_points={
        'console_scripts': [
            'dream=dreamer.cli:main'
        ]
    }
)
