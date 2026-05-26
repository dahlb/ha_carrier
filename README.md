# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/dahlb/ha_carrier/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                                                                 |    Stmts |     Miss |   Branch |   BrPart |   Cover |   Missing |
|--------------------------------------------------------------------- | -------: | -------: | -------: | -------: | ------: | --------: |
| custom\_components/ha\_carrier/\_\_init\_\_.py                       |      120 |       38 |       18 |        7 |     66% |67, 70-71, 130, 132-134, 139-150, 165-166, 185-205, 240, 243-246, 250, 252, 257, 273-\>278 |
| custom\_components/ha\_carrier/binary\_sensor.py                     |       54 |        2 |        6 |        2 |     93% |37-\>43, 77, 93 |
| custom\_components/ha\_carrier/carrier\_data\_update\_coordinator.py |      164 |       21 |       44 |        7 |     85% |157, 226, 231-232, 273, 320, 343-\>349, 353-360, 394-395, 401, 404-412, 446, 461-462 |
| custom\_components/ha\_carrier/carrier\_entity.py                    |       80 |        9 |       24 |        9 |     83% |70-77, 112, 127-\>126, 129-130, 144-\>143, 146-147, 187, 213-\>215 |
| custom\_components/ha\_carrier/climate.py                            |      237 |       31 |       90 |       21 |     81% |64-\>67, 67-\>69, 69-\>71, 71-\>74, 137, 157-165, 185, 192, 217, 227, 232, 249, 323-\>exit, 334-335, 365-374, 399, 438-\>441, 485, 490-491, 493-494, 497 |
| custom\_components/ha\_carrier/config\_flow.py                       |      127 |       12 |       38 |       12 |     85% |60-\>67, 63-64, 68-69, 154-\>171, 161, 197-198, 213, 221, 265-\>294, 267-268, 280-\>285, 286 |
| custom\_components/ha\_carrier/const.py                              |       31 |        0 |        0 |        0 |    100% |           |
| custom\_components/ha\_carrier/diagnostics.py                        |       32 |        0 |        8 |        2 |     95% |63-\>46, 80-\>87 |
| custom\_components/ha\_carrier/exceptions.py                         |        2 |        0 |        0 |        0 |    100% |           |
| custom\_components/ha\_carrier/migrate.py                            |      230 |       43 |      110 |       26 |     76% |98, 114, 127-131, 179, 183-185, 189, 223, 225-233, 244-249, 253, 272-281, 286-295, 300, 346-\>353, 411-\>414, 414-\>416, 416-\>418, 418-\>421, 435-\>447, 443, 458-\>447, 491, 500-509, 514-523, 595, 670-\>684, 673-681 |
| custom\_components/ha\_carrier/resiliency.py                         |      104 |        8 |       30 |        6 |     90% |167-173, 260, 266, 273, 279, 283, 289 |
| custom\_components/ha\_carrier/select.py                             |       52 |        6 |        8 |        4 |     83% |66, 82, 111, 127, 158-159 |
| custom\_components/ha\_carrier/sensor.py                             |      354 |       60 |      106 |       41 |     78% |76-\>82, 82-\>88, 88-\>94, 117-\>131, 171, 188, 288-290, 295-296, 300-302, 306-\>310, 307-\>306, 311-317, 326-333, 363-364, 368-370, 374-\>378, 375-\>374, 379-385, 420-421, 425-427, 431-\>430, 433-\>430, 437, 468-469, 473-475, 481-\>478, 485, 516-517, 521-523, 529-\>526, 533, 565, 621-622, 650-651, 679-680, 738-739, 793, 802, 804-\>exit, 832-833, 838, 870-871 |
| custom\_components/ha\_carrier/util.py                               |       82 |        6 |       38 |        7 |     89% |124, 128, 176, 179, 187, 212, 235-\>230 |
| **TOTAL**                                                            | **1669** |  **236** |  **520** |  **144** | **81%** |           |


## Setup coverage badge

Below are examples of the badges you can use in your main branch `README` file.

### Direct image

[![Coverage badge](https://raw.githubusercontent.com/dahlb/ha_carrier/python-coverage-comment-action-data/badge.svg)](https://htmlpreview.github.io/?https://github.com/dahlb/ha_carrier/blob/python-coverage-comment-action-data/htmlcov/index.html)

This is the one to use if your repository is private or if you don't want to customize anything.

### [Shields.io](https://shields.io) Json Endpoint

[![Coverage badge](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/dahlb/ha_carrier/python-coverage-comment-action-data/endpoint.json)](https://htmlpreview.github.io/?https://github.com/dahlb/ha_carrier/blob/python-coverage-comment-action-data/htmlcov/index.html)

Using this one will allow you to [customize](https://shields.io/endpoint) the look of your badge.
It won't work with private repositories. It won't be refreshed more than once per five minutes.

### [Shields.io](https://shields.io) Dynamic Badge

[![Coverage badge](https://img.shields.io/badge/dynamic/json?color=brightgreen&label=coverage&query=%24.message&url=https%3A%2F%2Fraw.githubusercontent.com%2Fdahlb%2Fha_carrier%2Fpython-coverage-comment-action-data%2Fendpoint.json)](https://htmlpreview.github.io/?https://github.com/dahlb/ha_carrier/blob/python-coverage-comment-action-data/htmlcov/index.html)

This one will always be the same color. It won't work for private repos. I'm not even sure why we included it.

## What is that?

This branch is part of the
[python-coverage-comment-action](https://github.com/marketplace/actions/python-coverage-comment)
GitHub Action. All the files in this branch are automatically generated and may be
overwritten at any moment.