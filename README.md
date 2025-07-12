# Trainlog

Online trips diary

## How to run the application

To set up the application, you need to provide certain configuration details. We use a YAML file for this purpose.

### Set up

Install python dependencies:

```bash
pip install -r requirements.txt
```

If you want to use pyenv to manage your Python virtual environment, you can automatically enable
it for the trainlog folder with:

```bash
pyenv install 3.9.18
pyenv virtualenv 3.9.18 trainlog
pyenv local trainlog
pip install -r requirements.txt
```

Install pre-commit hooks, to automatically format and lint your code:

```bash
pre-commit install
```

Copy the provided `config-example.yaml` and `.env.example`:

```bash
cp config-example.yaml config.yaml
cp .env.example .env
```

Edit the `.env` file with appropriate values.

Edit the `config.yaml` in your preferred text editor to fill in your details:

You'll also need to setup [Git LFS](https://git-lfs.com/).

```bash
git lfs install
git lfs pull
```

#### SMTP Configuration

This is for sending emails. You can ignore it for running the app locally.
Please provide the SMTP server details:

- `server`: The address of your SMTP server. Example: `smtp.gmail.com`
- `port`: The port used by the SMTP server. Common ports include `587` (with STARTTLS) or `465` (for SSL/TLS).
- `user`: The email address you'll be sending emails from.
- `password`: The password for the email account.

```yaml
smtp:
  server: smtp.example.com
  port: 587
  user: your_email@example.com
  password: YOUR_PASSWORD_HERE
```

#### Owner Configuration

This section is for the application owner's details:

- `username`: Your preferred username.
- `email`: Your email address.
- `password`: Your password (will be securely hashed before storing).

```yaml
owner:
  username: your_username
  email: your_email@example.com
  password: your_password
```

#### OpenAI configuration

OpenAI is used for bootstrapping translations. If you won't be doing that, you don't
need to configure that part.

```yaml
openai:
  openai_key: OPENAI_API_KEY
```

#### Google configuration

This is used to fetch photos of ships.

```yaml
google:
  key: GOOGLE_API_KEY
  cx: GOOGLE_CX
```

#### BuyMeaCoffee configuration

This is used for donations to the project. You can ignore this when running the app
locally.

```yaml
bmc:
  key: BMC_API_KEY
```

### Run the app

```bash
make start
```

Or if you prefer to run Flask outside of docker:

```bash
make start-local
```

### Important Notes

- **Security**: Ensure that the `config.yaml` file is secured with the right file permissions and is not publicly accessible. Keep it out of version control to prevent accidentally exposing sensitive details.
- **Placeholder Configuration**: Do not use the `config-example.yaml` as is for the application. Always create a copy and provide real values.
- **Supported Python version**: currently Python 3.9, but not 3.10 (that would need some refactoring due to breaking changes in used libraries)
